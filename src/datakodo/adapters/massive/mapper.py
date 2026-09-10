"""Massive raw response -> canonical schema normalization.

All normalization uses vectorized operations via pandas. The OHLCV base shape
is the canonical invariant minimum (design doc sec 3): ``timestamp, open, high,
low, close, volume, is_closed``. Massive extras are exposed opt-in via the
``columns`` parameter (``session``, ``vwap``, ``trades_count``).
"""

from typing import Any

import pandas as pd

from datakodo.core.enums import AssetClass, InstrumentType
from datakodo.core.instruments import Instrument
from datakodo.core.schemas import Trade, resolve_ohlcv_columns
from datakodo.core.timeframe import timeframe_delta

# Optional columns Massive can supply on top of the canonical base (design sec 3).
MASSIVE_OHLCV_EXTRAS = ("session", "vwap", "trades_count")

# Markets whose bars are anchored to a trading session (non-24/7).
_SESSION_MARKETS = ("stocks", "index", "options", "futures")

# Crypto trade condition codes (socket XT.*): 1 = sell side, 2 = buy side.
_CRYPTO_SIDE = {1: "sell", 2: "buy"}

# Raw Massive agg key -> canonical column name. Any key NOT in here is
# passed through as-is, so new/asset-specific fields need no code change.
_RAW_MAP = {
    "t": "timestamp",
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
    "v": "volume",
    "vw": "vwap",
    "n": "trades_count",
    "otc": "otc",
}


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


def _safe_float(value: Any) -> float | None:
    """Convert to float, returning None instead of raising on None."""
    return float(value) if value is not None else None


def _session_for(market: str) -> str | None:
    """Session label for a Massive market; None for 24/7 markets.

    Massive stock aggregates fold pre/regular/post sessions into one bar, so the
    label is always ``"regular"`` for sessioned markets; crypto/forex trade
    around the clock and carry no session (design doc sec 3).
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

    Every key the endpoint returns is mapped (known keys via ``_RAW_MAP``,
    unknown keys passed through verbatim), so Forex/Crypto/Indices/Options
    differences need no code change. Massive does not send an open/closed
    flag, so it is derived: a bar whose open time plus one candle is still
    in the future is marked not closed (design sec 18).

    ``columns`` selects the schema - ``"basic"`` (default) returns the
    invariant minimum; ``"all"`` returns base plus every extra the endpoint
    actually provided; a list requests specific extras.
    """
    base = ["timestamp", "open", "high", "low", "close", "volume", "is_closed"]

    if not raw:
        return pd.DataFrame(
            {
                "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
                "open": pd.Series(dtype="float64"),
                "high": pd.Series(dtype="float64"),
                "low": pd.Series(dtype="float64"),
                "close": pd.Series(dtype="float64"),
                "volume": pd.Series(dtype="float64"),
                "is_closed": pd.Series(dtype="bool"),
            }
        )

    rows = []
    for r in raw:
        row: dict[str, Any] = {}
        # Map every key the endpoint sent; known keys get canonical names,
        # unknown keys pass through verbatim (dynamic per asset type).
        for key, value in r.items():
            name = _RAW_MAP.get(key, key)
            if name == "timestamp":
                row[name] = pd.Timestamp(_to_ms(value), unit="ms", tz="UTC")
            elif name in ("open", "high", "low", "close", "volume", "vwap"):
                row[name] = _safe_float(value)
            else:
                row[name] = value
        # Derived fields (not sent by endpoint).
        row["session"] = _session_for(market)
        rows.append(row)

    df = pd.DataFrame(rows)

    # Stitch pages: drop duplicate bars on timestamp so a next_url overlap
    # never produces a duplicate bar (design doc sec 12).
    if "timestamp" in df.columns:
        df = (
            df.drop_duplicates(subset="timestamp", keep="last")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

    # Ensure base columns exist even if endpoint omitted them.
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            df[col] = None
        df[col] = df[col].astype("float64")
    if "timestamp" not in df.columns:
        df["timestamp"] = pd.Series(dtype="datetime64[ns, UTC]")

    df["is_closed"] = df["timestamp"] + timeframe_delta(timeframe) <= pd.Timestamp.now(tz="UTC")

    # columns="all": base + every extra the endpoint actually provided.
    if columns == "all":
        extras = [c for c in df.columns if c not in base]
        ordered_extras = [c for c in ("session", "vwap", "trades_count", "otc") if c in extras]
        ordered_extras += [c for c in extras if c not in ordered_extras]
        return df[base + ordered_extras]

    # basic / explicit list: delegate to canonical resolver.
    available = tuple(c for c in MASSIVE_OHLCV_EXTRAS if c in df.columns)
    resolved = resolve_ohlcv_columns(columns, available)
    return df[[c for c in resolved if c in df.columns]]


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


def map_rest_trades(rows: list[dict[str, Any]]) -> list[Trade]:
    """Convert raw REST ``list_trades`` records to canonical ``Trade`` list.

    REST shape (unlike the socket shape in ``map_trades``)::

        {"price": float, "size": float, "sip_timestamp": ns,
         "participant_timestamp": ns, "id": str, "conditions": [...]}

    Timestamps are nanoseconds (``_to_ms`` scales down); stocks carry no
    aggressor side so ``side`` stays None. Non-numeric ``id`` values map to
    ``trade_id=None`` instead of raising.
    """
    trades: list[Trade] = []
    for r in rows:
        ts = r.get("sip_timestamp") or r.get("participant_timestamp") or r.get("t")
        raw_id = r.get("id")
        try:
            trade_id = int(raw_id) if raw_id is not None else None
        except (TypeError, ValueError):
            trade_id = None
        price = r.get("price")
        size = r.get("size")
        if price is None or size is None:
            continue
        trades.append(
            Trade(
                timestamp=pd.Timestamp(_to_ms(ts or 0), unit="ms", tz="UTC"),
                price=float(price),
                size=float(size),
                side=None,
                trade_id=trade_id,
            )
        )
    return trades


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
