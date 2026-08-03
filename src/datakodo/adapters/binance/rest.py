"""Binance REST client — wraps the ``python-binance`` library."""

import logging
from datetime import UTC, datetime
from typing import Any

from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException

from datakodo.core.exceptions import (
    AuthenticationError,
    ConnectionError,
    DataLibError,
    ProviderError,
    RateLimitError,
    SymbolNotFoundError,
)
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

    @staticmethod
    def _translate(exc: BinanceAPIException) -> DataLibError:
        """Map a ``BinanceAPIException`` to the DataKodo exception hierarchy."""
        code = exc.code or 0
        status = exc.status_code
        message = exc.message or exc.text

        if code == -1001:  # DISCONNECTED
            return ConnectionError(f"Binance connection lost: {message}")
        if code == -1121:  # INVALID_SYMBOL
            return SymbolNotFoundError(f"Binance symbol not found: {message}")
        if code in (-1022, -2014, -2015) or status in (401, 403):  # auth
            return AuthenticationError(f"Binance authentication failed: {message}")
        if code == -1003 or status in (418, 429):  # too many requests
            return RateLimitError(f"Binance rate limit: {message}")

        return ProviderError(f"Binance error ({code}): {message}", original=exc)

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
        try:
            return list(self._client.get_klines(**params))
        except BinanceAPIException as exc:
            raise self._translate(exc) from exc
        except BinanceRequestException as exc:
            raise ConnectionError(f"Binance request failed: {exc}") from exc
