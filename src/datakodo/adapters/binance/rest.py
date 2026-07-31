"""Binance REST client — wraps the ``python-binance`` library."""

import logging
from datetime import UTC, datetime
from typing import Any

from binance.client import Client

logger = logging.getLogger(__name__)


def _to_millis(dt: datetime) -> int:
    """Convert a (possibly naive) datetime to epoch milliseconds (UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


class BinanceREST:
    """Thin wrapper around python-binance's sync ``Client``.

    Only the public market-data endpoints DataKodo needs are exposed. The
    library's own exceptions (``BinanceAPIException`` etc.) propagate
    unchanged; they are mapped to DataKodo exceptions in a later step.
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        *,
        timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._client = Client(
            api_key,
            api_secret,
            requests_params={"timeout": timeout},
            ping=False,
        )

    def klines(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        limit: int = 1000,
    ) -> list:
        """Fetch raw Binance klines for ``symbol`` at ``interval``.

        One HTTP request for up to ``limit`` candles (Binance caps at 1000).
        ``start``/``end`` are optional; when given they are sent as
        ``startTime``/``endTime`` (epoch milliseconds). Returns the raw
        12-field kline rows; the mapper converts them to canonical OHLCV.
        """
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start is not None:
            params["startTime"] = _to_millis(start)
        if end is not None:
            params["endTime"] = _to_millis(end)
        logger.info(
            "Binance get_klines symbol=%s interval=%s start=%s end=%s limit=%s",
            symbol,
            interval,
            start,
            end,
            limit,
        )
        return list(self._client.get_klines(**params))
