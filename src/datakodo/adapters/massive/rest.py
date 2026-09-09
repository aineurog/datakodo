"""Massive REST client — one class on top of the official client.

The official ``massive.RESTClient`` (rebranded Polygon.io client) handles URL
construction, parameter serialisation, and model decoding. DataKodo adds the
pieces it hides: a token-bucket gate (design doc sec 17), DataKodo-configured
exponential-backoff retries (sec 16), and mapping of HTTP status codes onto
the DataKodo exception hierarchy.

``_get`` is the single choke point every endpoint flows through, so gating,
retry, and error mapping apply uniformly to single-shot and paginated calls.
Pagination stays on so list endpoints follow ``next_url`` automatically.

Requests are stateless, so one ``MassiveREST`` instance is safe to share
across threads (design doc sec 23).
"""

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from massive.rest import RESTClient
from urllib3.exceptions import HTTPError as Urllib3HTTPError
from urllib3.exceptions import TimeoutError as Urllib3TimeoutError

from datakodo.adapters.massive.config import MassiveConfig
from datakodo.core.config import Config
from datakodo.core.enums import Timeframe
from datakodo.core.exceptions import (
    AuthenticationError,
    ConnectionError,
    DataLibError,
    DataValidationError,
    InvalidTimeframeError,
    PaidTierRequiredError,
    ProviderError,
    RateLimitError,
    RetriesExhaustedError,
    SymbolNotFoundError,
    TimeoutError,
)
from datakodo.core.timeframe import MASSIVE_MAP
from datakodo.ratelimit.limiter import TokenBucket

logger = logging.getLogger(__name__)

# Aggregates are capped at 50 000 base bars per request (official docs).
_MAX_AGGS_LIMIT = 50_000


def massive_resolution(timeframe: Timeframe | str) -> tuple[int, str]:
    """Return the Massive ``(multiplier, timespan)`` pair for ``timeframe``."""
    if isinstance(timeframe, str):
        try:
            timeframe = Timeframe(timeframe)
        except ValueError as exc:
            raise ValueError(f"Unknown timeframe: {timeframe}") from exc
    resolution = MASSIVE_MAP.get(timeframe)
    if resolution is None:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    return resolution


def _to_millis(dt: datetime) -> int:
    """Convert a (possibly naive) datetime to epoch milliseconds (UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


class MassiveREST(RESTClient):
    """Official REST client plus DataKodo gating, retry, and error mapping.

    Exposes only the endpoints DataKodo needs; raw rows are returned exactly
    as the provider defines them and normalization stays in the mapper module.
    """

    def __init__(
        self,
        massive_config: MassiveConfig | None = None,
        config: Config | None = None,
        *,
        timeout: float | None = None,
        rate_limit: tuple[float, int] | None = None,
    ) -> None:
        self._config = config or Config()
        self._massive = massive_config or MassiveConfig()
        rate, burst = (
            rate_limit
            if rate_limit is not None
            else (self._massive.rate_limit_rate, self._massive.rate_limit_burst)
        )
        self._limiter = TokenBucket(rate=rate, burst=burst)
        super().__init__(
            api_key=self._massive.api_key,
            base=self._massive.base_url,
            connect_timeout=timeout or self._massive.timeout,
            read_timeout=timeout or self._massive.timeout,
            retries=0,  # DataKodo owns retry timing, not urllib3.
            pagination=True,  # follow next_url automatically.
        )

    @staticmethod
    def _server_message(resp: Any) -> str | None:
        """Extract the server's human-readable ``message`` field, if any.

        Keeps the useful part of an error body (e.g. which timeframe the
        plan excludes) while dropping protocol noise like ``request_id``.
        """
        data = getattr(resp, "data", b"")
        if not isinstance(data, bytes):
            return None
        try:
            body = json.loads(data.decode("utf-8"))
        except ValueError:
            return None
        message = body.get("message") if isinstance(body, dict) else None
        return str(message) if message else None

    def _translate_response(self, resp: Any) -> DataLibError:
        """Map a non-200 Massive response onto the DataKodo hierarchy.

        Messages are professional one-liners (design doc sec 16): what
        happened plus what to do — never a raw JSON dump.
        """
        status = getattr(resp, "status", 0)
        detail = self._server_message(resp)
        suffix = f" Server says: {detail}" if detail else ""

        if status == 400:
            return DataValidationError(f"Massive rejected the request (HTTP 400).{suffix}")
        if status == 401:
            return AuthenticationError("Massive authentication failed. Check MASSIVE_API_KEY.")
        if status == 403:
            return PaidTierRequiredError(
                "Massive endpoint requires a paid tier (HTTP 403). "
                f"Upgrade: https://massive.com/pricing.{suffix}"
            )
        if status == 404:
            return SymbolNotFoundError("Massive ticker not found (HTTP 404).")
        if status == 429:
            retry_after = 0.0
            headers = getattr(resp, "headers", None)
            if headers is not None and "Retry-After" in headers:
                try:
                    retry_after = float(headers.get("Retry-After"))
                except (TypeError, ValueError):
                    retry_after = 0.0
            return RateLimitError(
                f"Massive rate limit exceeded (HTTP 429). Retry after {retry_after:.0f}s.",
                retry_after=retry_after,
            )
        return ProviderError(f"Massive request failed (HTTP {status}).{suffix}")

    def _get(
        self,
        path: str,
        params: dict | None = None,
        result_key: str | None = None,
        deserializer=None,
        raw: bool = False,
        options=None,
    ) -> Any:
        """Choke-point GET: token gate, backoff retry, status mapping, decode."""
        headers = self._concat_headers(options.headers) if options is not None else self.headers
        max_retries = self._config.max_retries
        base_delay = self._config.retry_base_delay

        for attempt in range(max_retries + 1):
            if not self._limiter.consume(1):
                retry_after = self._limiter.wait_time(1)
                logger.info(
                    "Massive local rate limit (attempt %d/%d), retrying in %.1fs",
                    attempt + 1,
                    max_retries + 1,
                    retry_after,
                )
                if attempt < max_retries:
                    time.sleep(retry_after)
                    continue
                raise RetriesExhaustedError(
                    f"Massive request failed after {max_retries + 1} attempts "
                    f"(local rate limit, retry after {retry_after:.1f}s)."
                )

            try:
                resp = self.client.request(
                    "GET",
                    self.BASE + path,
                    fields=params,
                    headers=headers,
                    retries=False,  # surface raw status; DataKodo owns retry logic
                )
            except Urllib3HTTPError as exc:
                # Timeouts stay distinct from connection failures: they carry
                # different retry semantics (design doc sec 16).
                if isinstance(exc, Urllib3TimeoutError):
                    translated: DataLibError = TimeoutError(f"Massive request timed out: {exc}")
                else:
                    translated = ConnectionError(f"Massive connection failed: {exc}")
                if attempt < max_retries:
                    delay = base_delay * (2**attempt)
                    logger.info(
                        "Massive connection failed (attempt %d/%d), retrying in %.1fs",
                        attempt + 1,
                        max_retries + 1,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise translated from exc

            if resp.status == 200:
                break

            translated = self._translate_response(resp)
            if isinstance(translated, (RateLimitError, ProviderError)):
                if attempt < max_retries:
                    delay = base_delay * (2**attempt)
                    retry_after = max(delay, getattr(translated, "retry_after", 0.0))
                    logger.info(
                        "Massive HTTP %s (attempt %d/%d), retrying in %.1fs",
                        resp.status,
                        attempt + 1,
                        max_retries + 1,
                        retry_after,
                    )
                    time.sleep(retry_after)
                    continue
                raise RetriesExhaustedError(
                    f"Massive request failed after {max_retries + 1} attempts "
                    f"(last HTTP {resp.status})."
                ) from translated
            raise translated

        if raw:
            return resp

        try:
            obj = self.json.loads(resp.data.decode("utf-8"))
        except ValueError:
            return []

        if result_key:
            if result_key not in obj:
                return []
            obj = obj[result_key]
        if deserializer:
            obj = [deserializer(o) for o in obj] if isinstance(obj, list) else deserializer(obj)
        return obj

    # -- OHLCV --

    def aggs(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        adjust: bool = True,
        limit: int = 5_000,
    ) -> list:
        """Fetch raw Massive aggregates for ``symbol`` over ``start`` -> ``end``.

        Returns raw rows with the compact Massive keys (``t, o, h, l, c, v,
        vw, n, otc``); the mapper converts them to canonical OHLCV.
        """
        try:
            multiplier, timespan = massive_resolution(timeframe)
        except ValueError as exc:
            raise InvalidTimeframeError(
                f"Massive does not support timeframe {timeframe!r}"
            ) from exc

        from_ms = _to_millis(start)
        to_ms = _to_millis(end)
        logger.info(
            "Fetching Massive aggs for %s [%s -> %s] timeframe=%s adjust=%s",
            symbol,
            start.isoformat(),
            end.isoformat(),
            timeframe,
            adjust,
        )

        agg_limit = max(1, min(limit, _MAX_AGGS_LIMIT))
        rows: list[dict] = []
        for agg in self.list_aggs(
            ticker=symbol,
            multiplier=multiplier,
            timespan=timespan,
            from_=from_ms,
            to=to_ms,
            adjusted=adjust,
            sort="asc",
            limit=agg_limit,
        ):
            rows.append(
                {
                    "t": agg.timestamp,
                    "o": agg.open,
                    "h": agg.high,
                    "l": agg.low,
                    "c": agg.close,
                    "v": agg.volume,
                    "vw": agg.vwap,
                    "n": agg.transactions,
                    "otc": agg.otc,
                }
            )
        return rows

    # -- Reference / instrument discovery (design doc sec 5) --

    def list_tickers(
        self,
        *,
        market: str | None = None,
        type: str | None = None,
        exchange: str | None = None,
        active: bool | None = None,
        search: str | None = None,
        currency: str | None = None,
        limit: int = 1_000,
        params: dict | None = None,
    ) -> list:
        """Fetch the Massive ticker reference, following ``next_url``."""
        extra_params = dict(params or {})
        if currency is not None:
            extra_params["currency"] = currency

        logger.info(
            "Fetching Massive tickers market=%s type=%s exchange=%s active=%s "
            "search=%s currency=%s limit=%s",
            market,
            type,
            exchange,
            active,
            search,
            currency,
            limit,
        )
        tickers = super().list_tickers(
            type=type,
            market=market,
            exchange=exchange,
            active=active,
            search=search,
            limit=max(1, min(limit, 1_000)),
            sort="ticker",
            order="asc",
            params=extra_params or None,
        )
        return [dict(vars(t)) for t in tickers]

    def ensure_symbol_known(self, symbol: str) -> str:
        """Normalize *symbol* for downstream calls.

        Unlike MT5 there is no terminal watchlist to select into; existence
        is proven by the following ``ticker_details`` call (404 maps to
        ``SymbolNotFoundError``). Returns the stripped input unchanged.
        """
        return symbol.strip()

    def ticker_details(self, ticker: str) -> dict:
        """Fetch the single-ticker overview for ``ticker`` as a raw dict."""
        logger.info("Fetching Massive ticker details for %s", ticker)
        details = self.get_ticker_details(ticker=ticker)
        return dict(vars(details)) if details else {}

    # -- Historical ticks (design doc sec 7: chunked, opt-in) --

    def list_trades(
        self,
        ticker: str,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        limit: int = 1_000,
    ) -> list:
        """Fetch raw historical trades for ``ticker``; often paid-tier gated."""
        logger.info(
            "Fetching Massive trades for %s [%s -> %s] limit=%s",
            ticker,
            start.isoformat() if start else None,
            end.isoformat() if end else None,
            limit,
        )
        trades = super().list_trades(
            ticker=ticker,
            timestamp_gte=_to_millis(start) if start is not None else None,
            timestamp_lte=_to_millis(end) if end is not None else None,
            limit=max(1, min(limit, 50_000)),
            sort="timestamp",
            order="asc",
        )
        return [dict(vars(t)) for t in trades]
