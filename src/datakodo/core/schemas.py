"""Canonical data models — the single source of truth for output schemas.

Every adapter converts raw provider responses into these fixed shapes.
All models are Pydantic-based for validation, type safety, and serialization.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from datakodo.core.enums import AssetClass, InstrumentType


class OHLCV(BaseModel):
    """Canonical OHLCV candle.

    Every adapter's fetch_ohlcv() must return data conforming to this shape.
    """

    schema_version: str = "1.0"
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    session: str | None = Field(default=None)


class Trade(BaseModel):
    """Canonical trade/tick record."""

    schema_version: str = "1.0"
    timestamp: datetime
    price: float
    size: float
    side: str | None = Field(default=None)


class OrderBookLevel(BaseModel):
    """Single price level in an order book."""

    schema_version: str = "1.0"
    price: float
    size: float


class OrderBook(BaseModel):
    """Canonical order book snapshot."""

    schema_version: str = "1.0"
    timestamp: datetime
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]


class Fundamentals(BaseModel):
    """Canonical fundamentals / reference data (design doc sec 3, 6).

    Uses a minimal common base (fields true across asset classes) plus an
    optional asset-class-specific block, mirroring the Instrument design
    (sec 4). Providers that expose more detail subclass or extend the
    asset-class block; the base stays stable.
    """

    schema_version: str = "1.0"
    symbol: str
    asset_class: AssetClass | None = None
    instrument_type: InstrumentType | None = None
    currency: str | None = None
    exchange: str | None = None
    latest_price: float | None = None
    price_change_24h: float | None = None
    open_24h: float | None = None
    high_24h: float | None = None
    low_24h: float | None = None
    volume_24h: float | None = None
    quote_volume_24h: float | None = None
    timestamp: datetime | None = None

    crypto: "CryptoFundamentals | None" = None


class ReferenceData(BaseModel):
    """Common base for asset-class-specific reference data."""

    schema_version: str = "1.0"


class CryptoFundamentals(ReferenceData):
    """Crypto-specific fundamentals from Binance exchange info / ticker.

    ``status`` reflects trading status; ``permissions`` lists the trading
    modes (e.g. ``["SPOT"]`` or ``["TRADING"]`` for futures).
    """

    base_asset: str = ""
    quote_asset: str = ""
    status: str = ""
    is_spot_trading_allowed: bool | None = None
    is_margin_trading_allowed: bool | None = None
    permissions: list[str] = []
    asset_class: AssetClass = AssetClass.CRYPTO
    instrument_type: InstrumentType = InstrumentType.SPOT
