"""Massive REST client — thin wrapper around the official ``massive`` SDK.

The SDK (``massive.RESTClient``) already handles auth, retries, and
``next_url`` pagination internally. This wrapper only:

  * lazily builds the client with the configured API key,
  * throttles calls through the per-instance token bucket, and
  * maps the SDK's two exceptions (``AuthError``, ``BadResponse``) onto the
    DataKodo exception hierarchy.

Raw SDK models are returned as-is; all mapping to canonical schemas happens
in ``mapper.py`` (shared with the WebSocket path).
"""

import json
import logging
from datetime import UTC, datetime

from massive import RESTClient
from massive.exceptions import AuthError, BadResponse

from datakodo.core.config import Config
from datakodo.core.exceptions import (
    AuthenticationError,
    DataLibError,
    DataNotAvailableError,
    PaidTierRequiredError,
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


def _to_nanos(dt: datetime) -> int:
    """Convert a (possibly naive) datetime to epoch nanoseconds (UTC).

    Massive's v3 trades/quotes filter on nanosecond timestamps; reuses
    ``_to_millis`` so tz handling stays in one place.
    """
    return _to_millis(dt) * 1_000_000


def translate_status(body: str) -> DataLibError:
    """Map a Massive ``BadResponse`` body onto the DataKodo hierarchy.

    Massive wraps plan/tier/auth errors in a JSON body with a ``status``
    field (e.g. ``NOT_AUTHORIZED``, ``NOT_FOUND``). Falls back to a generic
    ``DataLibError`` when the body is not JSON.
    """
    try:
        payload = json.loads(body)
        status = payload.get("status")
        message = payload.get("message") or ""
    except (ValueError, AttributeError):
        status, message = None, body

    if status == "NOT_AUTHORIZED":
        lowered = message.lower()
        if "api key" in lowered or "credential" in lowered or "authenticat" in lowered:
            return AuthenticationError(f"Massive authentication failed: {message or body[:200]}")
        pricing = "see https://massive.com/pricing"
        if "pricing" not in lowered:
            message = f"{message} Upgrade to a paid tier — {pricing}"
        return PaidTierRequiredError(f"Massive: {message or body[:200]}")
    if status in ("FORBIDDEN", "FORBIDDEN_FOR_YOU"):
        return DataNotAvailableError(f"Massive: {message or body[:200]}")
    if status == "NOT_FOUND":
        return SymbolNotFoundError(f"Massive symbol not found: {message or body[:200]}")
    if status in ("TOO_MANY_REQUESTS", "TOO_MANY_REQUESTS_PER_SECOND"):
        return RateLimitError(f"Massive rate limit exceeded: {message or body[:200]}")
    return DataLibError(f"Massive API error: {body[:200]}")


class MassiveREST:
    """Thin wrapper around the massive SDK's ``RESTClient``.

    Exposes only the public market-data endpoints DataKodo needs. Calls are
    gated by a token bucket, and SDK exceptions are mapped onto DataKodo
    exceptions.
    """

    def __init__(
        self,
        api_key: str = "",
        *,
        timeout: float | None = None,
        rate_limit: tuple[float, int] | None = None,
        config: Config | None = None,
    ) -> None:
        cfg = config or Config()
        if api_key:
            cfg = cfg.model_copy(update={"massive_api_key": api_key})
        self._config = cfg
        rate, burst = (
            rate_limit
            if rate_limit is not None
            else (cfg.massive_rate_limit_rate, cfg.massive_rate_limit_burst)
        )
        self._limiter = TokenBucket(rate=rate, burst=burst)
        self._timeout = timeout if timeout is not None else cfg.massive_timeout
        self._client: RESTClient | None = None

    def _get_client(self) -> RESTClient:
        """Build the SDK client on first use, raising if no API key."""
        if self._client is None:
            if not self._config.massive_api_key:
                raise AuthenticationError(
                    "Massive requires an API key. Set MASSIVE_API_KEY in .env "
                    "or pass api_key= to the adapter."
                )
            self._client = RESTClient(
                api_key=self._config.massive_api_key,
                read_timeout=self._timeout,
                retries=self._config.max_retries,
            )
        return self._client

    def _acquire(self) -> None:
        """Consume one token, raising if the bucket is empty."""
        if not self._limiter.consume(1):
            retry_after = self._limiter.wait_time(1)
            raise RateLimitError(
                f"Massive rate limit exceeded. Retry after {retry_after:.1f}s.",
                retry_after=retry_after,
            )

    def aggs(
        self,
        symbol: str,
        multiplier: int,
        timespan: str,
        start: datetime,
        end: datetime,
        *,
        limit: int = 50000,
        adjusted: bool = True,
    ) -> list:
        """Fetch raw aggregate (OHLCV) bars for ``symbol``.

        ``list_aggs`` follows ``next_url`` internally so the full range is
        returned in one call. ``start``/``end`` are sent as epoch milliseconds
        (UTC). Returns the SDK ``Agg`` models; the mapper converts them.
        """
        self._acquire()
        logger.info(
            "Massive aggs symbol=%s multiplier=%s timespan=%s start=%s end=%s",
            symbol,
            multiplier,
            timespan,
            start,
            end,
        )
        try:
            iterator = self._get_client().list_aggs(
                ticker=symbol,
                multiplier=multiplier,
                timespan=timespan,
                from_=_to_millis(start),
                to=_to_millis(end),
                adjusted=adjusted,
                sort="asc",
                limit=limit,
            )
            return list(iterator)
        except AuthError as exc:
            raise AuthenticationError(f"Massive authentication failed: {exc}") from exc
        except BadResponse as exc:
            raise translate_status(str(exc)) from exc

    def futures_aggs(
        self,
        symbol: str,
        resolution: str,
        start: datetime,
        end: datetime,
        *,
        limit: int = 50000,
    ) -> list:
        """Fetch raw aggregate bars for a futures contract (``/futures/v1/aggs``).

        Futures use a ``resolution`` string (e.g. ``1min``, ``1session``) and
        ``window_start`` filters in nanoseconds; ``list_futures_aggregates``
        follows ``next_url`` internally. Returns the SDK ``FuturesAgg`` models;
        the mapper converts them (handling ``window_start`` in ns).
        """
        self._acquire()
        logger.info(
            "Massive futures aggs symbol=%s resolution=%s start=%s end=%s",
            symbol,
            resolution,
            start,
            end,
        )
        try:
            iterator = self._get_client().list_futures_aggregates(
                ticker=symbol,
                resolution=resolution,
                window_start_gte=_to_nanos(start),
                window_start_lte=_to_nanos(end),
                sort="asc",
                limit=limit,
            )
            return list(iterator)
        except AuthError as exc:
            raise AuthenticationError(f"Massive authentication failed: {exc}") from exc
        except BadResponse as exc:
            raise translate_status(str(exc)) from exc

    def trades(
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        limit: int = 50000,
    ) -> list:
        """Fetch raw v3 trade records for ``symbol``.

        ``start``/``end`` are sent as nanosecond ``timestamp.gte``/``.lte``
        filters; ``list_trades`` follows ``next_url`` internally so the full
        range is returned in one call. Returns the SDK ``Trade`` models
        (timestamps in ns); the mapper converts them.
        """
        self._acquire()
        logger.info(
            "Massive trades symbol=%s start=%s end=%s limit=%s",
            symbol,
            start,
            end,
            limit,
        )
        try:
            iterator = self._get_client().list_trades(
                ticker=symbol,
                timestamp_gte=_to_nanos(start) if start is not None else None,
                timestamp_lte=_to_nanos(end) if end is not None else None,
                limit=limit,
            )
            return list(iterator)
        except AuthError as exc:
            raise AuthenticationError(f"Massive authentication failed: {exc}") from exc
        except BadResponse as exc:
            raise translate_status(str(exc)) from exc
