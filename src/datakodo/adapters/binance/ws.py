"""Binance WebSocket streaming — real-time trade and order book feeds."""

import logging
from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


class BinanceWS:
    """Async WebSocket client for Binance real-time streams."""

    STREAM_BASE = "wss://stream.binance.com:9443/ws"

    def __init__(self) -> None:
        pass

    async def trade_stream(self, symbol: str) -> AsyncGenerator:
        """Yield raw trade messages for *symbol*.

        Mapped to canonical Trade schema by the mapper module.
        """
        logger.info("Starting Binance trade stream for %s", symbol)
        raise NotImplementedError("Binance WebSocket client not yet implemented")
        yield  # pragma: no cover — unreachable until implemented

    async def orderbook_stream(self, symbol: str) -> AsyncGenerator:
        """Yield raw order book snapshots + deltas for *symbol*."""
        logger.info("Starting Binance order book stream for %s", symbol)
        raise NotImplementedError("Binance WebSocket client not yet implemented")
        yield  # pragma: no cover — unreachable until implemented
