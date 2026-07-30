"""Canonical data models — the single source of truth for output schemas.

Every adapter converts raw provider responses into these fixed shapes.
"""

from datetime import datetime


class OHLCV:
    """Canonical OHLCV candle.

    Every adapter's fetch_ohlcv() must return data conforming to this shape.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    session: str | None = None


class Trade:
    """Canonical trade/tick record."""

    timestamp: datetime
    price: float
    size: float
    side: str | None = None


class OrderBookLevel:
    """Single price level in an order book."""

    price: float
    size: float


class OrderBook:
    """Canonical order book snapshot."""

    timestamp: datetime
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
