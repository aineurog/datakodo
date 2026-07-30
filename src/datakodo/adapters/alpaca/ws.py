"""Alpaca WebSocket streaming — real-time trade and order book feeds."""

import logging
from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


class AlpacaWS:
    """Async WebSocket client for Alpaca real-time streams."""

    def __init__(self, api_key: str = "", api_secret: str = "") -> None:
        self._api_key = api_key
        self._api_secret = api_secret

    async def trade_stream(self, symbol: str) -> AsyncGenerator:
        """Yield raw trade messages for *symbol*.

        Mapped to canonical Trade schema by the mapper module.
        """
        logger.info("Starting Alpaca trade stream for %s", symbol)
        raise NotImplementedError("Alpaca WebSocket client not yet implemented")
        yield  # pragma: no cover

    async def orderbook_stream(self, symbol: str) -> AsyncGenerator:
        """Yield raw order book snapshots + deltas for *symbol*."""
        logger.info("Starting Alpaca order book stream for %s", symbol)
        raise NotImplementedError("Alpaca WebSocket client not yet implemented")
        yield  # pragma: no cover
