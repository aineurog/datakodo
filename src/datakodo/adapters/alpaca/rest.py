"""Alpaca REST transport: official SDK plus DataKodo gating and error mapping.

``_call`` is the single choke point every endpoint flows through, so the
token-bucket gate, backoff retries, and status mapping apply uniformly.
"""

import logging
import time
from datetime import datetime
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.crypto import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
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

_CLIENTS: dict[str, Any] = {
    "stock": StockHistoricalDataClient,
    "crypto": CryptoHistoricalDataClient,
    "assets": TradingClient,
}


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
        self._clients: dict[str, Any] = {}

    def _ensure_client(self, kind: str = "stock"):
        """Build the SDK client on first use (lazy: SDK rejects empty keys)."""
        if kind not in self._clients:
            if not (self._alpaca.api_key and self._alpaca.api_secret):
                raise AuthenticationError(
                    "Alpaca credentials are missing. Pass api_key/api_secret "
                    "explicitly or set APCA_API_KEY_ID / APCA_API_SECRET_KEY."
                )
            extra: dict[str, Any] = (
                {"paper": True}  # assets catalog is identical on both hosts
                if kind == "assets"
                else {"url_override": self._alpaca.data_base_url}
            )
            self._clients[kind] = _CLIENTS[kind](
                api_key=self._alpaca.api_key, secret_key=self._alpaca.api_secret, **extra
            )
        return self._clients[kind]

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
                "Alpaca authentication failed. Check APCA_API_KEY_ID / APCA_API_SECRET_KEY "
                "(paper keys — reference reads use the paper host)."
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

    def _call(self, method: str, *args: Any, client: str = "stock", **kwargs: Any) -> Any:
        """Choke-point call: token gate, backoff retry, status mapping."""
        target = self._ensure_client(client)
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
                return getattr(target, method)(*args, **kwargs)
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

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        feed: str | None = None,
        adjustment: str = "raw",
    ) -> list:
        """Fetch raw SDK bars (stocks → ``get_stock_bars``, ``X/Y`` → crypto)."""
        resolution = alpaca_resolution(timeframe)
        if "/" in symbol:
            result = self._call(
                "get_crypto_bars",
                CryptoBarsRequest(
                    symbol_or_symbols=symbol, timeframe=resolution, start=start, end=end
                ),
                client="crypto",
            )
        else:
            result = self._call(
                "get_stock_bars",
                StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=resolution,
                    start=start,
                    end=end,
                    feed=DataFeed(feed or self._alpaca.feed),
                    adjustment=Adjustment(adjustment),
                ),
            )
        try:
            return list(result[symbol])
        except KeyError:
            return []

    def list_assets(self) -> list:
        """Fetch the assets master list (fresh per call, no caching)."""
        return list(self._call("get_all_assets", client="assets"))
