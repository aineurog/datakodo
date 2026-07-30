"""Polygon REST client — wraps Polygon.io REST endpoints."""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class PolygonREST:
    """Thin HTTP wrapper around Polygon.io REST API."""

    BASE_URL = "https://api.polygon.io"

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    def aggs(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list:
        """Fetch aggregate bars (candles) for *symbol*.

        Returns raw Polygon aggregates. Mapped to canonical OHLCV by
        the mapper module.
        """
        logger.info(
            "Fetching Polygon aggregates for %s [%s → %s] timeframe=%s",
            symbol,
            start.isoformat(),
            end.isoformat(),
            timeframe,
        )
        raise NotImplementedError("Polygon REST client not yet implemented")
