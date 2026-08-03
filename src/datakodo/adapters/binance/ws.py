"""Binance WebSocket streaming — real-time trade and order book feeds.

Implemented with python-binance's ``AsyncClient`` + ``BinanceSocketManager``.
Each stream method is an async generator: it opens a socket for the symbol and
yields raw JSON messages (spot or USD-M futures) as they arrive.
"""

import logging
from collections.abc import AsyncIterator

from binance import AsyncClient, BinanceSocketManager

logger = logging.getLogger(__name__)


class BinanceWS:
    """Async WebSocket client for Binance real-time streams."""

    def __init__(self, api_key: str = "", api_secret: str = "") -> None:
        self._api_key = api_key
        self._api_secret = api_secret

    async def _client(self) -> AsyncClient:
        return await AsyncClient.create(self._api_key, self._api_secret)

    async def _messages(self, symbol: str, market_type: str, depth: bool) -> AsyncIterator:
        """Open the relevant socket and yield its decoded messages."""
        client = await self._client()
        manager = BinanceSocketManager(client)
        if depth:
            socket = (
                manager.futures_depth_socket(symbol)
                if market_type == "futures"
                else manager.depth_socket(symbol)
            )
        else:
            socket = (
                manager.aggtrade_futures_socket(symbol)
                if market_type == "futures"
                else manager.aggtrade_socket(symbol)
            )

        try:
            async with socket as stream:
                while True:
                    message = await stream.recv()
                    # Futures multiplex sockets wrap the payload under a "data" key.
                    yield message["data"] if isinstance(message, dict) and "data" in message else message
        finally:
            await client.close_connection()

    async def trade_stream(self, symbol: str, market_type: str = "spot") -> AsyncIterator:
        """Yield raw aggregate-trade messages for ``symbol`` (spot or futures)."""
        logger.info("Starting Binance trade stream for %s (%s)", symbol, market_type)
        async for msg in self._messages(symbol, market_type, depth=False):
            yield msg

    async def orderbook_stream(self, symbol: str, market_type: str = "spot") -> AsyncIterator:
        """Yield raw order book depth messages for ``symbol`` (spot or futures)."""
        logger.info("Starting Binance order book stream for %s (%s)", symbol, market_type)
        async for msg in self._messages(symbol, market_type, depth=True):
            yield msg
