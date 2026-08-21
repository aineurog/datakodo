"""Massive raw response -> canonical schema normalization.

All normalization uses vectorized operations via pandas. The OHLCV base shape
is the canonical invariant minimum (design doc sec 3): ``timestamp, open, high,
low, close, volume, is_closed``. Massive extras are exposed opt-in via the
``columns`` parameter (``session``, ``vwap``, ``trades_count``).
"""

from typing import Any

import pandas as pd

from datakodo.core.schemas import Trade, resolve_ohlcv_columns
from datakodo.core.enums import AssetClass, InstrumentType
from datakodo.core.instruments import Instrument
from datakodo.core.timeframe import timeframe_delta

# Optional columns Massive can supply on top of the canonical base (design sec 3).
MASSIVE_OHLCV_EXTRAS = ("session", "vwap", "trades_count")

# Markets whose bars are anchored to a trading session (non-24/7).
_SESSION_MARKETS = ("stocks", "index", "options", "futures")

# Crypto trade condition codes (socket XT.*): 1 = sell side, 2 = buy side.
_CRYPTO_SIDE = {1: "sell", 2: "buy"}


def _to_ms(value: int | str) -> int:
    """Normalize a Unix timestamp to milliseconds.

    Aggregates and socket messages carry milliseconds; v3 trades/quotes and
    futures carry nanoseconds. Values above 10^15 ms (year 33658) are treated
    as nanoseconds and scaled down.
    """
    value = int(value)
    if abs(value) > 10**15:
        return value // 1_000_000
    return value


def _session_for(market: str) -> str | None:
    """Session label for a Massive market; None for 24/7 markets.

    Massive stock aggregates fold pre/regular/post sessions into one bar, so the
    label is always ``"regular"`` for sessioned markets; crypto/forex trade
    around the clock and carry no session (design sec 3).
    """
    return "regular" if market in _SESSION_MARKETS else None


def map_ohlcv(
    raw: list,
    timeframe: str,
    *,
    market: str = "stocks",
    columns=None,
) -> pd.DataFrame:
    """Convert raw Massive aggregates into a canonical OHLCV DataFrame.

    Massive agg format::

        {"t": unix_ms, "o": open, "h": high, "l": low, "c": close,
         "v": volume, "vw": vwap, "n": trades, "otc": bool}

    Massive does not send an open/closed flag, so it is derived: a bar whose
    open time plus one candle is still in the future is marked not closed
    (design sec 18). ``columns`` selects the schema - ``"basic"`` (default),
    ``"all"``, or an explicit list of extra columns.
    """
    available = MASSIVE_OHLCV_EXTRAS
    resolved = resolve_ohlcv_columns(columns, available)

    if not raw:
        return pd.DataFrame(columns=resolved)

    rows = []
    for r in raw:
        rows.append(
            {
                "timestamp": pd.Timestamp(_to_ms(r["t"]), unit="ms", tz="UTC"),
                "open": float(r["o"]),
                "high": float(r["h"]),
                "low": float(r["l"]),
                "close": float(r["c"]),
                "volume": float(r["v"]),
                "session": _session_for(market),
                "vwap": r.get("vw"),
                "trades_count": r.get("n"),
            }
        )
    df = pd.DataFrame(rows)
    df["is_closed"] = df["timestamp"] + timeframe_delta(timeframe) <= pd.Timestamp.now(tz="UTC")
    return df[resolved]


def map_instrument(symbol: str, ticker: dict[str, Any], market: str | None = None) -> Instrument:
    """Classify a Massive ticker dict into a canonical ``Instrument``.

    Massive ticker list fields -> ``Instrument`` mapping (design doc sec 6.2):

    - ``ticker``        -> ``symbol``, ``provider_symbol`` (prefixed form verbatim)
    - ``market``        -> ``asset_class``  (stocks->equity, crypto->crypto,
      fx->forex, indices->index, options->equity, futures->asset_class from
      underlying root)
    - ``type``          -> ``instrument_type`` (equities spot, options parsed
      from OCC, futures future, crypto spot/perpetual)
    - ``primary_exchange`` -> ``exchange``  (raw code)
    - ``currency_name`` -> ``currency``     (uppercased)
    - typed extensions  -> left as None; filled lazily via
      ``ticker_details()`` when a specific symbol is selected (design doc
      sec 6.3).

    The ``asset_class`` and ``instrument_type`` are derived from the Massive
    ``market`` and ``type`` fields as follows:

    - ``stocks``       -> ``asset_class=EQUITY``, ``instrument_type=SPOT``
    - ``crypto``       -> ``asset_class=CRYPTO``, ``instrument_type=SPOT`` or
      ``PERPETUAL`` (if ``type`` contains ``perpetual``)
    - ``forex``        -> ``asset_class=FOREX``, ``instrument_type=SPOT``
    - ``indices``      -> ``asset_class=INDEX``, ``instrument_type=SPOT``
    - ``options``      -> ``asset_class=EQUITY``, ``instrument_type=OPTION``
    - ``futures``      -> ``asset_class=EQUITY``, ``instrument_type=FUTURE``
    """
    raw_ticker = (ticker.get("ticker") or "").upper()
    name = ticker.get("name") or ""
    massive_market = (ticker.get("market") or "").lower()
    massive_type = (ticker.get("type") or "").lower()
    exchange = ticker.get("primary_exchange") or ""
    currency_name = (ticker.get("currency_name") or "").upper()

    # -- asset_class -------------------------------------------------------
    if massive_market == "stocks":
        asset_class = AssetClass.EQUITY
        instrument_type = InstrumentType.SPOT
    elif massive_market == "crypto":
        asset_class = AssetClass.CRYPTO
        if "perpetual" in massive_type:
            instrument_type = InstrumentType.PERPETUAL
        else:
            instrument_type = InstrumentType.SPOT
    elif massive_market == "forex":
        asset_class = AssetClass.FOREX
        instrument_type = InstrumentType.SPOT
    elif massive_market == "indices":
        asset_class = AssetClass.INDEX
        instrument_type = InstrumentType.SPOT
    elif massive_market == "options":
        asset_class = AssetClass.EQUITY
        instrument_type = InstrumentType.OPTION
    elif massive_market == "futures":
        asset_class = AssetClass.EQUITY
        instrument_type = InstrumentType.FUTURE
    else:
        asset_class = AssetClass.EQUITY
        instrument_type = InstrumentType.SPOT

    # -- instrument_type from ``type`` field refinement --------------------
    if massive_type == "option":
        instrument_type = InstrumentType.OPTION
    elif massive_type == "future":
        instrument_type = InstrumentType.FUTURE

    # -- currency ----------------------------------------------------------
    currency = currency_name if currency_name else "USD"

    # -- build Instrument --------------------------------------------------
    inst = Instrument(
        symbol=raw_ticker,
        provider_symbol=raw_ticker,
        exchange=exchange or "Massive",
        currency=currency,
        asset_class=asset_class,
        instrument_type=instrument_type,
    )

    # typed extensions are left as None; they are filled lazily via
    # ``ticker_details()`` when a specific symbol is selected (design doc sec 6.3)
    return inst


def map_trades(raw: dict[str, Any]) -> Trade:
    """Convert a raw Massive trade message into a canonical Trade.

    Handles both the stocks (``T.*``) and crypto (``XT.*``) shapes:

    - timestamp: ``t`` (ms) or ``sip_timestamp`` (ns); normalized to ms.
    - price/size: ``p``/``s``.
    - side: crypto encodes the aggressor side numerically (``c`` 1 = sell,
      2 = buy); stocks use condition-code lists that do not encode side, so it
      is left unknown there.
    - id: ``i`` (trade id) where present.
    """
    timestamp = raw.get("t") or raw.get("sip_timestamp") or raw.get("timestamp")
    conditions = raw.get("c")

    if isinstance(conditions, int):
        side = _CRYPTO_SIDE.get(conditions)
    else:
        side = None

    return Trade(
        timestamp=pd.Timestamp(_to_ms(timestamp or 0), unit="ms", tz="UTC"),
        price=float(raw["p"]),
        size=float(raw.get("s") or 0.0),
        side=side,
        trade_id=raw.get("i"),
    )