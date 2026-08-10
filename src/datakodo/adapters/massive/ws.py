"""Massive WebSocket streaming — real-time trade feeds."""

import logging
from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


class MassiveWS:
    """Async WebSocket client for Massive.com real-time streams."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    async def trade_stream(self, symbol: str) -> AsyncGenerator:
        """Yield raw trade messages for *symbol*.

        Mapped to canonical Trade schema by the mapper module.
        """
        logger.info("Starting Massive trade stream for %s", symbol)
        raise NotImplementedError("Massive WebSocket client not yet implemented")
        yield  # pragma: no cover
