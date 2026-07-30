"""Binance REST client — wraps Binance public endpoints."""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class BinanceREST:
    """Thin HTTP wrapper around Binance public REST endpoints."""

    BASE_URL = "https://api.binance.com"

    def __init__(self, api_key: str = "", api_secret: str = "") -> None:
        self._api_key = api_key
        self._api_secret = api_secret

    def klines(self, symbol: str, interval: str, start: datetime, end: datetime) -> list:
        """Fetch kline/candlestick data.

        Returns raw Binance kline list. Mapped to canonical OHLCV by
        the mapper module.
        """
        logger.info(
            "Fetching Binance klines for %s [%s → %s] interval=%s",
            symbol,
            start.isoformat(),
            end.isoformat(),
            interval,
        )
        raise NotImplementedError("Binance REST client not yet implemented")
