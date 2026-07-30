"""IBKR WebSocket streaming — real-time market data feeds.

IBKR delivers real-time data via the same TWS socket connection.
This module manages the async subscription and streaming for
live trade ticks and order book depth.
"""

import logging
from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


class IBKRWS:
    """Async streaming client for IBKR real-time market data.

    Shares the TWS socket connection managed by IBKRClient but
    exposes an async generator interface for streaming consumers.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id

    async def trade_stream(self, symbol: str) -> AsyncGenerator:
        """Yield real-time trade ticks for *symbol*.

        Mapped to canonical Trade schema by the mapper module.
        """
        logger.info("Starting IBKR trade stream for %s", symbol)
        raise NotImplementedError("IBKR trade streaming not yet implemented")
        yield  # pragma: no cover

    async def orderbook_stream(self, symbol: str) -> AsyncGenerator:
        """Yield real-time order book depth updates for *symbol*."""
        logger.info("Starting IBKR order book stream for %s", symbol)
        raise NotImplementedError("IBKR order book streaming not yet implemented")
        yield  # pragma: no cover
