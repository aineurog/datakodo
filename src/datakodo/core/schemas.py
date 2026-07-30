"""Canonical data models — the single source of truth for output schemas.

Every adapter converts raw provider responses into these fixed shapes.
All models are Pydantic-based for validation, type safety, and serialization.
"""

from datetime import datetime

from pydantic import BaseModel, Field


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
