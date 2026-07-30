"""Alpaca REST client — wraps Alpaca Market Data API."""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class AlpacaREST:
    """Thin HTTP wrapper around Alpaca Market Data REST endpoints."""

    BASE_URL = "https://data.alpaca.markets"

    def __init__(self, api_key: str = "", api_secret: str = "") -> None:
        self._api_key = api_key
        self._api_secret = api_secret

    def bars(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list:
        """Fetch historical bars for *symbol*.

        Returns raw Alpaca bar list. Mapped to canonical OHLCV by
        the mapper module.
        """
        logger.info(
            "Fetching Alpaca bars for %s [%s → %s] timeframe=%s",
            symbol,
            start.isoformat(),
            end.isoformat(),
            timeframe,
        )
        raise NotImplementedError("Alpaca REST client not yet implemented")
