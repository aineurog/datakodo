"""Alpaca raw response → canonical schema normalization."""

import pandas as pd
from alpaca.trading.enums import AssetClass as AlpacaAssetClass

from datakodo.core.calendar import CRYPTO_UTC, XNYS_REGULAR
from datakodo.core.enums import (
    AssetClass,
    InstrumentType,
    PriceAdjustment,
    VolumeType,
)
from datakodo.core.instruments import Instrument, InstrumentCapabilities
from datakodo.core.schemas import Trade, resolve_ohlcv_columns
from datakodo.ops.validation import bar_close_times

_ALPACA_CAPABILITIES = InstrumentCapabilities(
    ohlcv=True,
    ticks=True,
    streaming_ticks=True,
    real_volume=True,
    adjusted_prices=True,
)

# Alpaca's adjustment enum -> canonical price-adjustment label.
_ADJUSTMENT = {
    "raw": PriceAdjustment.RAW,
    "split": PriceAdjustment.SPLIT,
    "dividend": PriceAdjustment.UNKNOWN,
    "all": PriceAdjustment.SPLIT_AND_DIVIDEND,
}


def price_adjustment(adjustment: str) -> PriceAdjustment:
    """Map an Alpaca ``adjustment`` value to the canonical label."""
    return _ADJUSTMENT.get(str(adjustment).lower(), PriceAdjustment.UNKNOWN)


_ALPACA_CLASS = {
    AlpacaAssetClass.US_EQUITY: (AssetClass.EQUITY, InstrumentType.SPOT),
    AlpacaAssetClass.CRYPTO: (AssetClass.CRYPTO, InstrumentType.SPOT),
    AlpacaAssetClass.US_OPTION: (AssetClass.EQUITY, InstrumentType.OPTION),
    AlpacaAssetClass.CRYPTO_PERP: (AssetClass.CRYPTO, InstrumentType.PERPETUAL),
}


def _utc(value) -> pd.Timestamp:
    """Normalize to a UTC-aware pandas Timestamp."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def map_ohlcv(raw: list, timeframe: str, *, session: str | None = "regular", columns="basic"):
    """SDK bars → canonical OHLCV frame (UTC bar-open ``timestamp``)."""
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
    for b in raw:
        row = {
            "timestamp": _utc(b.timestamp),
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": float(b.volume),
            "vwap": None if b.vwap is None else float(b.vwap),
            "trades_count": None if b.trade_count is None else int(b.trade_count),
        }
        # 24/7 crypto has no session; an all-None column would imply a
        # session boundary that does not exist (calendar doc sec 4).
        if session is not None:
            row["session"] = session
        rows.append(row)
    df = pd.DataFrame(rows)

    df = (
        df.drop_duplicates(subset="timestamp", keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    for col in ("open", "high", "low", "close", "volume", "vwap"):
        df[col] = df[col].astype("float64")
    df["trades_count"] = df["trades_count"].astype("Int64")

    df["is_closed"] = bar_close_times(df["timestamp"], timeframe) <= pd.Timestamp.now(tz="UTC")

    if columns == "all":
        extras = [c for c in df.columns if c not in base]
        return df[base + extras]
    available = tuple(c for c in ("session", "vwap", "trades_count") if c in df.columns)
    return df[resolve_ohlcv_columns(columns, available)]


def map_instrument(symbol: str, asset, *, data_tier: str = "") -> Instrument:
    """SDK Asset → canonical Instrument (class × type never conflated).

    ``data_tier`` names the market-data feed the client is entitled to
    (``iex`` on the free tier, ``sip`` with a paid plan). Alpaca's security
    master is tier independent, so the adapter passes its configured feed.
    """
    asset_class, instrument_type = _ALPACA_CLASS.get(
        asset.asset_class, (AssetClass.EQUITY, InstrumentType.SPOT)
    )
    crypto = asset_class is AssetClass.CRYPTO
    if crypto:
        volume_type = VolumeType.BASE
        session_profile, calendar = CRYPTO_UTC, "CRYPTO_UTC"
    else:
        volume_type = VolumeType.SHARES
        session_profile, calendar = XNYS_REGULAR, "XNYS"
    if instrument_type is InstrumentType.OPTION:
        volume_type = VolumeType.CONTRACTS
    exchange = getattr(asset.exchange, "value", asset.exchange) or "Alpaca"
    return Instrument(
        symbol=symbol,
        provider_symbol=symbol,
        exchange=exchange,
        currency="USD",
        asset_class=asset_class,
        instrument_type=instrument_type,
        volume_type=volume_type,
        session_profile=session_profile,
        calendar=calendar,
        data_tier=data_tier,
        capabilities=_ALPACA_CAPABILITIES,
    )


def trade_of(r) -> Trade:
    """One SDK trade → canonical ``Trade`` (shared by REST list and WS stream)."""
    return Trade(
        timestamp=_utc(r.timestamp),
        price=float(r.price),
        size=float(r.size),
        side=None,
        trade_id=r.id,
    )


def map_rest_trades(rows: list) -> list[Trade]:
    """SDK trades → canonical ``Trade`` list (REST shape, not the WS one).

    REST trades carry no aggressor side, so ``side`` is always None.
    Price/size/id come pre-validated from the SDK model.
    """
    return [trade_of(r) for r in rows]
