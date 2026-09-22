"""Alpaca REST transport: official SDK plus DataKodo gating and error mapping.

``_call`` is the single choke point every endpoint flows through, so the
token-bucket gate, backoff retries, and status mapping apply uniformly.
"""

import logging
import time
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from requests.exceptions import RequestException
from requests.exceptions import Timeout as RequestsTimeout

from datakodo.adapters.alpaca.config import AlpacaConfig
from datakodo.core.config import Config
from datakodo.core.enums import Timeframe
from datakodo.core.exceptions import (
    AuthenticationError,
    ConnectionError,
    DataLibError,
    DataValidationError,
    PaidTierRequiredError,
    ProviderError,
    RateLimitError,
    RetriesExhaustedError,
    SymbolNotFoundError,
    TimeoutError,
)
from datakodo.core.timeframe import ALPACA_MAP
from datakodo.ratelimit.limiter import TokenBucket

logger = logging.getLogger(__name__)


def alpaca_resolution(timeframe: Timeframe | str) -> TimeFrame:
    """Return the Alpaca ``TimeFrame`` for a canonical ``timeframe``."""
    if isinstance(timeframe, str):
        try:
            timeframe = Timeframe(timeframe)
        except ValueError as exc:
            raise ValueError(f"Unknown timeframe: {timeframe}") from exc
    resolution = ALPACA_MAP.get(timeframe)
    if resolution is None:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    amount, unit = resolution
    return TimeFrame(amount, TimeFrameUnit[unit])


class AlpacaREST:
    """Official-SDK transport with DataKodo gating, retry, and error mapping."""

    def __init__(
        self,
        alpaca_config: AlpacaConfig | None = None,
        config: Config | None = None,
    ) -> None:
        self._config = config or Config()
        self._alpaca = alpaca_config or AlpacaConfig()
        self._limiter = TokenBucket(
            rate=self._alpaca.rate_limit_rate,
            burst=self._alpaca.rate_limit_burst,
        )
        self._client: StockHistoricalDataClient | None = None

    def _ensure_client(self) -> StockHistoricalDataClient:
        """Build the SDK client on first use (lazy: SDK rejects empty keys)."""
        if self._client is None:
            if not (self._alpaca.api_key and self._alpaca.api_secret):
                raise AuthenticationError(
                    "Alpaca credentials are missing. Pass api_key/api_secret "
                    "explicitly or set APCA_API_KEY_ID / APCA_API_SECRET_KEY."
                )
            self._client = StockHistoricalDataClient(
                api_key=self._alpaca.api_key,
                secret_key=self._alpaca.api_secret,
                url_override=self._alpaca.data_base_url,
            )
        return self._client

    def _translate(self, exc: APIError) -> DataLibError:
        """Map an SDK error onto the DataKodo hierarchy."""
        status = exc.status_code
        try:
            detail = str(exc.message)
        except (ValueError, KeyError, TypeError):
            detail = str(exc)
        suffix = f" Server says: {detail}" if detail else ""

        if status in (400, 422):
            return DataValidationError(f"Alpaca rejected the request (HTTP {status}).{suffix}")
        if status == 401:
            return AuthenticationError(
                "Alpaca authentication failed. Check APCA_API_KEY_ID / APCA_API_SECRET_KEY."
            )
        if status == 403:
            return PaidTierRequiredError(
                "Alpaca endpoint requires a paid data tier (HTTP 403). "
                f"See your Alpaca dashboard to upgrade.{suffix}"
            )
        if status == 404:
            return SymbolNotFoundError("Alpaca symbol not found (HTTP 404).")
        if status == 429:
            headers = getattr(getattr(exc, "response", None), "headers", None) or {}
            try:
                retry_after = float(headers.get("Retry-After", 0.0))
            except (TypeError, ValueError):
                retry_after = 0.0
            return RateLimitError(
                f"Alpaca rate limit exceeded (HTTP 429). Retry after {retry_after:.0f}s.",
                retry_after=retry_after,
            )
        if isinstance(status, int) and status >= 500:
            return ProviderError(f"Alpaca request failed (HTTP {status}).{suffix}")
        label = f" (HTTP {status})" if status else ""
        return ProviderError(f"Alpaca request failed{label}.{suffix}", original=exc)

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Choke-point call: token gate, backoff retry, status mapping."""
        client = self._ensure_client()
        max_retries = self._config.max_retries
        base_delay = self._config.retry_base_delay

        for attempt in range(max_retries + 1):
            if not self._limiter.consume(1):
                retry_after = self._limiter.wait_time(1)
                if attempt < max_retries:
                    logger.info(
                        "Alpaca local rate limit (attempt %d/%d), retrying in %.1fs",
                        attempt + 1,
                        max_retries + 1,
                        retry_after,
                    )
                    time.sleep(retry_after)
                    continue
                raise RetriesExhaustedError(
                    f"Alpaca request failed after {max_retries + 1} attempts "
                    f"(local rate limit, retry after {retry_after:.1f}s)."
                )

            try:
                return getattr(client, method)(*args, **kwargs)
            except APIError as exc:
                translated = self._translate(exc)
                if isinstance(translated, (RateLimitError, ProviderError)):
                    if attempt < max_retries:
                        delay = base_delay * (2**attempt)
                        wait = max(delay, getattr(translated, "retry_after", 0.0))
                        logger.info(
                            "Alpaca %s (attempt %d/%d), retrying in %.1fs",
                            translated.__class__.__name__,
                            attempt + 1,
                            max_retries + 1,
                            wait,
                        )
                        time.sleep(wait)
                        continue
                    raise RetriesExhaustedError(
                        f"Alpaca request failed after {max_retries + 1} attempts "
                        f"(last {translated.__class__.__name__})."
                    ) from translated
                raise translated
            except RequestsTimeout as exc:
                # Timeout vs connection failure: different retry semantics.
                if attempt < max_retries:
                    delay = base_delay * (2**attempt)
                    logger.info(
                        "Alpaca request timed out (attempt %d/%d), retrying in %.1fs",
                        attempt + 1,
                        max_retries + 1,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise TimeoutError(f"Alpaca request timed out: {exc}") from exc
            except RequestException as exc:
                if attempt < max_retries:
                    delay = base_delay * (2**attempt)
                    logger.info(
                        "Alpaca connection failed (attempt %d/%d), retrying in %.1fs",
                        attempt + 1,
                        max_retries + 1,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise ConnectionError(f"Alpaca connection failed: {exc}") from exc
