"""Binance REST client — wraps the ``python-binance`` library."""

import logging
from datetime import UTC, datetime
from typing import Any

from binance.client import Client

from datakodo.core.exceptions import RateLimitError
from datakodo.ratelimit.limiter import TokenBucket

logger = logging.getLogger(__name__)


def _to_millis(dt: datetime) -> int:
    """Convert a (possibly naive) datetime to epoch milliseconds (UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


class BinanceREST:
    """Thin wrapper around python-binance's sync ``Client``.

    Only the public market-data endpoints DataKodo needs are exposed. Requests
    are gated by a token bucket, and the library's own exceptions are mapped
    to DataKodo exceptions.
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        *,
        timeout: float = 10.0,
        rate_limit: tuple[float, int] | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        rate, burst = rate_limit if rate_limit is not None else (100.0, 1000)
        self._limiter = TokenBucket(rate=rate, burst=burst)
        self._client = Client(
            api_key,
            api_secret,
            requests_params={"timeout": timeout},
            ping=False,
        )

    @staticmethod
    def _klines_weight(limit: int) -> int:
        """Binance spot kline request weight for a given ``limit``."""
        if limit <= 100:
            return 1
        if limit <= 500:
            return 2
        return 5

    def _acquire(self, weight: int) -> None:
        """Consume request weight, raising if the token bucket is empty."""
        if not self._limiter.consume(weight):
            retry_after = self._limiter.wait_time(weight)
            raise RateLimitError(
                f"Binance rate limit exceeded. Retry after {retry_after:.1f}s.",
                retry_after=retry_after,
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
        self._acquire(self._klines_weight(limit))
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
