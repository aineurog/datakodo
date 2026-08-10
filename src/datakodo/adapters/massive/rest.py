"""Massive REST client — wraps the Massive.com REST endpoints."""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class MassiveREST:
    """Thin HTTP wrapper around the Massive.com REST API."""

    BASE_URL = "https://api.massive.com"

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    def aggs(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list:
        """Fetch aggregate bars (candles) for *symbol*.

        Returns raw Massive aggregates. Mapped to canonical OHLCV by
        the mapper module.
        """
        logger.info(
            "Fetching Massive aggregates for %s [%s → %s] timeframe=%s",
            symbol,
            start.isoformat(),
            end.isoformat(),
            timeframe,
        )
        raise NotImplementedError("Massive REST client not yet implemented")
