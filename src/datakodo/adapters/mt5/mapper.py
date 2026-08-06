"""MT5 raw response → canonical schema normalization.

All normalization uses vectorized operations via pandas/polars.
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
)

logger = logging.getLogger(__name__)

# MT5 CopyRates volume baselines. MT5 returns both ``tick_volume`` and
# ``real_volume``. For forex/CFD instruments ``real_volume`` is usually 0
# because brokers do not report true traded volume, whereas ``tick_volume``
# counts every price-change tick and is reliably populated. ``tick_volume``
# is therefore the default; the choice is exposed as an explicit parameter.
VOLUME_BASELINES = ("tick_volume", "real_volume")


def map_ohlcv(raw, volume: str = "tick_volume", offset_seconds: int = 0) -> pd.DataFrame:
    """Convert raw MT5 rates into a DataFrame of canonical OHLCV rows.

    MT5 CopyRates returns a numpy structured array with named columns
    'time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread',
    'real_volume'. Raw ``time`` values are in **server time**; subtracting
    ``offset_seconds`` (see ``MT5Terminal.server_offset_seconds``) before
    ``utc=True`` yields true UTC.

    ``volume`` selects the canonical ``volume`` baseline:
    - ``"tick_volume"`` (default) — reliable for forex/CFDs.
    - ``"real_volume"`` — broker-traded volume (often 0 for forex).

    Returns a DataFrame with columns matching the OHLCV schema.
    """
    if volume not in VOLUME_BASELINES:
        raise ProviderError(
            f"Invalid volume baseline {volume!r}; expected one of {VOLUME_BASELINES}"
        )

    if raw is None or len(raw) == 0:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "session"]
        )

    df = pd.DataFrame(raw)
    df = df.rename(
        columns={
            "time": "timestamp",
            volume: "volume",
        }
    )

    # Server time → true UTC epoch, then to UTC-aware timestamps.
    df["timestamp"] = pd.to_datetime(df["timestamp"] - offset_seconds, unit="s", utc=True)
    df["session"] = "n/a"

    cols = ["timestamp", "open", "high", "low", "close", "volume", "session"]
    return df[cols]


# --- instrument classification (spot vs futures) ------------------------------


def map_instrument(
    symbol: str,
    info,
    futures_modes: frozenset[int] = frozenset(),
    market_type: str = "",
) -> Instrument:
    """Classify an MT5 ``SymbolInfo`` tuple into a canonical ``Instrument``.

    MT5 symbols are self-describing: ``trade_calc_mode`` reports how the
    broker prices the instrument (forex, futures, CFD, ...) and ``path``
    mirrors the Market Watch tree (``Forex\\EURUSD``, ``Futures\\...``).
    ``futures_modes`` holds the package's ``SYMBOL_CALC_MODE_*`` integers
    that denote futures contracts (values differ across builds, so they are
    resolved from the live module — see ``MT5Terminal.futures_calc_modes``).

    ``market_type`` is an optional user hint (``"spot"``/``"futures"``).
    When given it is validated against the detected classification: a
    mismatch raises ``ProviderError`` so spot-vs-futures confusion surfaces
    loudly instead of returning a silently wrong descriptor.
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
    elif base in ("XAU", "XAG", "XPT", "XPD"):
        # Metals are priced like forex (calc_mode 0 on some brokers) but are
        # a distinct asset class; detect them by the base currency first.
        instrument = Instrument(
            symbol=symbol,
            provider_symbol=symbol,
            exchange=exchange,
            currency=currency,
            asset_class=AssetClass.METAL,
            instrument_type=InstrumentType.SPOT,
        )
    elif calc_mode in _FOREX_MODES or path.startswith("forex"):
        instrument = _as_forex(symbol, info, exchange, currency)
    elif "crypto" in path or "coin" in path:
        instrument = Instrument(
            symbol=symbol,
            provider_symbol=symbol,
            exchange=exchange,
            currency=currency,
            asset_class=AssetClass.CRYPTO,
            instrument_type=InstrumentType.SPOT,
        )
    elif "commod" in path:
        instrument = Instrument(
            symbol=symbol,
            provider_symbol=symbol,
            exchange=exchange,
            currency=currency,
            asset_class=AssetClass.CFD,
            instrument_type=InstrumentType.CFD,
        )
    elif "indices" in path or "index" in path:
        instrument = Instrument(
            symbol=symbol,
            provider_symbol=symbol,
            exchange=exchange,
            currency=currency,
            asset_class=AssetClass.CFD,
            instrument_type=InstrumentType.CFD,
        )
    elif "equit" in path or "stocks" in path or "shares" in path:
        instrument = Instrument(
            symbol=symbol,
            provider_symbol=symbol,
            exchange=exchange,
            currency=currency,
            asset_class=AssetClass.EQUITY,
            instrument_type=InstrumentType.SPOT,
        )
    else:
        instrument = Instrument(
            symbol=symbol,
            provider_symbol=symbol,
            exchange=exchange,
            currency=currency,
            asset_class=AssetClass.CFD,
            instrument_type=InstrumentType.CFD,
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


# Symbol calc modes that denote spot forex pairs (values stable across
# builds: FOREX=0, FOREX_NO_LEVERAGE=5). Futures modes come from the live
# module because their numeric values changed between package versions.
_FOREX_MODES = frozenset((0, 5))


def _as_futures(symbol: str, info, exchange: str, currency: str) -> Instrument:
    """Build a FUTURE Instrument from a SymbolInfo tuple."""
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
        asset_class=AssetClass.FUTURE,
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
