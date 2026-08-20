"""MT5 raw response → canonical schema normalization.

All normalization uses vectorized operations via pandas.
"""

import datetime as dt
import logging

import pandas as pd

from datakodo.core.enums import AssetClass, InstrumentType
from datakodo.core.exceptions import ProviderError
from datakodo.core.instruments import (
    ForexExtension,
    FutureExtension,
    Instrument,
    MetalExtension,
)
from datakodo.core.schemas import Fundamentals

logger = logging.getLogger(__name__)

# MT5 CopyRates volume baselines. MT5 returns both ``tick_volume`` and
# ``real_volume``. For forex/CFD instruments ``real_volume`` is usually 0
# because brokers do not report true traded volume, whereas ``tick_volume``
# counts every price-change tick and is reliably populated. ``tick_volume``
# is therefore the default; the choice is exposed as an explicit parameter.
VOLUME_BASELINES = ("tick_volume", "real_volume")

# Extra MT5-only OHLCV columns, opt-in via ``columns`` (design doc sec 3).
# These mirror raw CopyRates fields that have no home in the canonical base:
# ``spread`` (quoted spread) and ``real_volume`` (broker-traded volume).
MT5_OHLCV_EXTRAS = ("spread", "real_volume")

# Metal base currencies MT5 uses for precious metals (quoted in troy ounces).
_METAL_BASES = ("XAU", "XAG", "XPT", "XPD")


def map_ohlcv(
    raw,
    volume: str = "tick_volume",
    offset_seconds: int = 0,
    extras: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Convert raw MT5 rates into a DataFrame of canonical OHLCV rows.

    MT5 CopyRates returns a numpy structured array with named columns
    'time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread',
    'real_volume'. Raw ``time`` values are in **server time**; subtracting
    ``offset_seconds`` (see ``MT5REST.server_offset_seconds``) before
    ``utc=True`` yields true UTC.

    ``volume`` selects the canonical ``volume`` baseline:
    - ``"tick_volume"`` (default) — reliable for forex/CFDs.
    - ``"real_volume"`` — broker-traded volume (often 0 for forex).

    ``extras`` appends opt-in MT5-only columns (``MT5_OHLCV_EXTRAS``) that
    the raw array carries but the canonical base drops, so ``columns="all"``
    can surface every field MT5 returns (design doc sec 3).

    Returns the base OHLCV columns (``timestamp, open, high, low, close,
    volume``) plus any requested extras. ``is_closed`` is added by the adapter
    (design doc sec 3).
    """
    if volume not in VOLUME_BASELINES:
        raise ProviderError(
            f"Invalid volume baseline {volume!r}; expected one of {VOLUME_BASELINES}"
        )

    base_cols = ["timestamp", "open", "high", "low", "close", "volume"]
    unknown = [extra for extra in extras if extra not in MT5_OHLCV_EXTRAS]
    if unknown:
        raise ProviderError(
            f"Invalid extra column(s) {unknown}; expected none or one of {MT5_OHLCV_EXTRAS}"
        )

    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=base_cols + [e for e in MT5_OHLCV_EXTRAS if e in extras])

    df = pd.DataFrame(raw)
    # Snapshot requested extras before the rename below consumes one baseline.
    extra_values = {e: df[e].values for e in MT5_OHLCV_EXTRAS if e in extras}
    df = df.rename(
        columns={
            "time": "timestamp",
            volume: "volume",
        }
    )

    # Server time → true UTC epoch, then to UTC-aware timestamps.
    df["timestamp"] = pd.to_datetime(df["timestamp"] - offset_seconds, unit="s", utc=True)

    out = df[base_cols].copy()
    for extra, values in extra_values.items():
        out[extra] = values
    return out


# --- instrument classification (spot vs futures) -----------------------------


def map_instrument(
    symbol: str,
    info,
    futures_modes: frozenset[int] = frozenset(),
    forex_modes: frozenset[int] = frozenset((0, 5)),
    market_type: str = "",
) -> Instrument:
    """Classify an MT5 ``SymbolInfo`` tuple into a canonical ``Instrument``.

    MT5 symbols are self-describing: ``trade_calc_mode`` reports how the
    broker prices the instrument (forex, futures, CFD, ...) and ``path``
    mirrors the Market Watch tree (``Forex\\EURUSD``, ``Futures\\...``).
    ``futures_modes`` holds the package's ``SYMBOL_CALC_MODE_*`` integers
    that denote futures contracts and ``forex_modes`` the ones that denote
    spot forex pairs (values differ across builds, so they are resolved from
    the live module — see ``MT5REST.futures_calc_modes`` and
    ``MT5REST.forex_calc_modes``).

    ``market_type`` is an optional user hint (``"spot"``/``"futures"``/...).
    When given it is validated against the detected classification: a
    mismatch raises ``ProviderError`` so spot-vs-futures confusion surfaces
    loudly instead of returning a silently wrong descriptor.

    The asset class and instrument type are distinct dimensions (design doc
    sec 4): ``CFD`` is an *instrument type*, never an asset class.
    """
    if info is None:
        raise ProviderError(f"No MT5 symbol info for {symbol!r}.")

    calc_mode = getattr(info, "trade_calc_mode", None)
    path = str(getattr(info, "path", "") or "").lower()
    exchange = getattr(info, "exchange", "") or "MetaTrader 5"
    base = getattr(info, "currency_base", "") or ""
    profit = getattr(info, "currency_profit", "") or ""
    currency = profit or base

    is_futures = calc_mode in futures_modes or "future" in path
    if is_futures:
        instrument = _as_futures(symbol, info, exchange, currency)
    elif base in _METAL_BASES:
        instrument = Instrument(
            symbol=symbol,
            provider_symbol=symbol,
            exchange=exchange,
            currency=currency,
            asset_class=AssetClass.METAL,
            instrument_type=InstrumentType.SPOT,
            metal=MetalExtension(unit="oz"),
        )
    elif calc_mode in forex_modes or path.startswith("forex"):
        instrument = _as_forex(symbol, info, exchange, currency)
    else:
        # Crypto, indices, equities, bonds, and best-effort fallbacks all
        # resolve through the same classifier (no divergent path checks).
        # ``SPOT`` for exchange-traded classes, ``CFD`` otherwise; ``CFD`` is
        # the instrument type for anything index-like (design doc sec 4).
        asset_class = _classify_asset(base, path)
        instrument = Instrument(
            symbol=symbol,
            provider_symbol=symbol,
            exchange=exchange,
            currency=currency,
            asset_class=asset_class,
            instrument_type=(
                InstrumentType.SPOT
                if asset_class in (AssetClass.EQUITY, AssetClass.CRYPTO)
                else InstrumentType.CFD
            ),
        )

    if market_type:
        # Accept the common plural alias ("futures") for InstrumentType.FUTURE.
        normalized = "future" if market_type == "futures" else market_type
        try:
            expected = InstrumentType(normalized)
        except ValueError:
            raise ProviderError(f"Unknown market_type={market_type!r} for {symbol!r}.") from None
        if instrument.instrument_type != expected:
            raise ProviderError(
                f"{symbol!r} is {instrument.instrument_type.value}, "
                f"not {expected.value} (requested market_type={market_type!r})."
            )
    return instrument


def _classify_asset(base: str, path: str) -> AssetClass:
    """Derive the canonical asset class from MT5 symbol cues.

    ``CFD``/``FUTURE`` are instrument types, so the asset class is derived
    from the underlying instead: precious-metal base currencies are METAL;
    indices, equities, crypto, and bond path/descriptions map to their own
    classes. Unknowns default to the index-like catch-all.
    """
    if base in _METAL_BASES:
        return AssetClass.METAL
    if "index" in path or "indices" in path:
        return AssetClass.INDEX
    if "equit" in path or "stocks" in path or "shares" in path:
        return AssetClass.EQUITY
    if "crypto" in path or "coin" in path:
        return AssetClass.CRYPTO
    if "bond" in path:
        return AssetClass.BOND
    return AssetClass.INDEX


def _as_futures(symbol: str, info, exchange: str, currency: str) -> Instrument:
    """Build a FUTURE Instrument from a SymbolInfo tuple."""
    path = str(getattr(info, "path", "") or "").lower()
    base = getattr(info, "currency_base", "") or ""
    expiry = _format_expiry(getattr(info, "expiration_time", 0))
    contract_size = float(getattr(info, "trade_contract_size", 0.0) or 0.0)
    tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    multiplier = tick_value / tick_size if tick_size else 0.0
    underlying = getattr(info, "description", "") or symbol
    return Instrument(
        symbol=symbol,
        provider_symbol=symbol,
        exchange=exchange,
        currency=currency,
        asset_class=_classify_asset(base, path),
        instrument_type=InstrumentType.FUTURE,
        future=FutureExtension(
            expiry=expiry,
            contract_size=contract_size,
            tick_size=tick_size,
            multiplier=multiplier,
            underlying=underlying,
        ),
    )


def _as_forex(symbol: str, info, exchange: str, currency: str) -> Instrument:
    """Build a FOREX Instrument with pip/lot sizing from a SymbolInfo tuple."""
    point = float(getattr(info, "point", 0.0) or 0.0)
    digits = int(getattr(info, "digits", 5) or 5)
    pip_size = point if point else 0.0001
    # 5-digit (and 3-digit JPY) quotes price in points that are 1/10 of a pip.
    if digits == 5 or digits == 3:
        pip_size = point * 10 if point else pip_size
    contract_size = float(getattr(info, "trade_contract_size", 0.0) or 0.0)
    lot_size = int(contract_size) if contract_size else 100_000
    return Instrument(
        symbol=symbol,
        provider_symbol=symbol,
        exchange=exchange,
        currency=currency,
        asset_class=AssetClass.FOREX,
        instrument_type=InstrumentType.SPOT,
        forex=ForexExtension(pip_size=pip_size, lot_size=lot_size),
    )


def _format_expiry(epoch: int | float) -> str:
    """Return an ISO date for a futures expiry epoch (0/empty → '')."""
    if not epoch:
        return ""
    return dt.datetime.fromtimestamp(epoch, tz=dt.UTC).date().isoformat()


# --- fundamentals ------------------------------------------------------------


def map_fundamentals(
    symbol: str,
    info,
    tick=None,
    futures_modes: frozenset[int] = frozenset(),
    forex_modes: frozenset[int] = frozenset((0, 5)),
    offset_seconds: int = 0,
) -> Fundamentals:
    """Build canonical ``Fundamentals`` from ``symbol_info`` (+ ``tick``).

    MT5 exposes reference data through ``SymbolInfo`` (currencies, description,
    classification). Classification is delegated to ``map_instrument`` so the
    asset class / instrument type stay consistent with ``MT5Adapter.instrument()``.
    ``as_of`` is the tick time converted to true UTC: ``tick.time`` is **server
    time**, so ``offset_seconds`` (see ``MT5REST.server_offset_seconds``) is
    subtracted first. MT5 has no live-price field on the canonical
    ``Fundamentals`` base for forex/metal classes yet, so price stats stay out
    of scope here (design doc sec 3).
    """
    if info is None:
        raise ProviderError(f"No MT5 symbol info for {symbol!r}.")
    inst = map_instrument(symbol, info, futures_modes=futures_modes, forex_modes=forex_modes)
    tick = tick or {}
    tick_time = getattr(tick, "time", None)
    return Fundamentals(
        symbol=symbol,
        name=getattr(info, "description", "") or symbol,
        asset_class=inst.asset_class,
        instrument_type=inst.instrument_type,
        currency=inst.currency,
        exchange=inst.exchange,
        as_of=_epoch_to_utc(tick_time - offset_seconds) if tick_time else None,
    )


def _epoch_to_utc(epoch) -> dt.datetime | None:
    """Return a UTC-aware datetime for a unix seconds value (None-safe)."""
    if not epoch:
        return None
    return dt.datetime.fromtimestamp(epoch, tz=dt.UTC)
