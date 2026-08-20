"""Massive REST client — wraps the official ``massive`` client for transport.

The official ``massive.RESTClient`` (rebranded Polygon.io client) handles URL
construction, parameter serialisation, and model decoding. DataKodo supplies
the pieces the official client hides: a token-bucket gate (design doc sec 17),
DataKodo-configured exponential-backoff retries (sec 16), and mapping of HTTP
status codes onto the DataKodo exception hierarchy (sec 2.8 of
``docs/implementation_step_massive.md``).

The official client's own urllib3 layer is used with ``retries=0`` so DataKodo
controls retry timing; pagination is left on so every list endpoint follows
``next_url`` through one shared helper (the official ``_paginate_iter``).

Requests are stateless, so one ``MassiveREST`` instance is safe to share
across threads (design doc sec 23).
"""

import logging
from datetime import UTC, datetime
from typing import Any

from massive.rest import RESTClient
from urllib3.exceptions import HTTPError as Urllib3HTTPError

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
)
from datakodo.core.timeframe import MASSIVE_MAP
from datakodo.ratelimit.limiter import TokenBucket

logger = logging.getLogger(__name__)

# Retryable server-side statuses (mirrors the official client's force-list,
# minus 413/499 which are equally retryable here).
_RETRYABLE_5XX = (500, 502, 503, 504)

# Aggregates are capped at 50 000 base bars per request (official docs).
_MAX_AGGS_LIMIT = 50_000


def massive_resolution(timeframe: Timeframe | str) -> tuple[int, str]:
    """Return the Massive ``(multiplier, timespan)`` pair for ``timeframe``.

    Accepts a ``Timeframe`` enum or a canonical string (``"1h"``, ``"1m"``).
    Raises ``ValueError`` for unknown timeframes.
    """
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


class _MassiveClient(RESTClient):
    """Official RESTClient with DataKodo gating, retry, and error mapping.

    Overrides ``_get`` - the single choke point every endpoint flows through -
    so the token bucket, retry loop, and status mapping apply uniformly to
    single-shot and paginated requests alike.
    """

    def __init__(
        self,
        api_key: str,
        base: str,
        connect_timeout: float,
        read_timeout: float,
        config: Config,
        limiter: TokenBucket,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base=base,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            retries=0,  # DataKodo owns retry timing, not urllib3.
            pagination=True,  # follow next_url via the shared helper.
        )
        self._config = config
        self._limiter = limiter

    def _translate_response(self, resp: Any) -> DataLibError:
        """Map a non-200 Massive response onto the DataKodo hierarchy."""
        status = getattr(resp, "status", 0)
        body = resp.data.decode("utf-8", errors="replace") if hasattr(resp, "data") else ""
        message = f"Massive HTTP {status}: {body[:200]}"

        if status == 400:
            return DataValidationError(message)
        if status == 401:
            return AuthenticationError(
                f"Massive authentication failed ({status}). Check MASSIVE_API_KEY. {body[:200]}"
            )
        if status == 403:
            return PaidTierRequiredError(
                f"Massive: endpoint requires a paid tier ({status}). {body[:200]}"
            )
        if status == 404:
            return SymbolNotFoundError(
                f"Massive ticker not found ({status}). {body[:200]}"
            )
        if status == 429:
            retry_after = 0.0
            headers = getattr(resp, "headers", None)
            if headers is not None and "Retry-After" in headers:
                try:
                    retry_after = float(headers.get("Retry-After"))
                except (TypeError, ValueError):
                    retry_after = 0.0
            return RateLimitError(message, retry_after=retry_after)
        if status in _RETRYABLE_5XX:
            return ProviderError(message)
        return ProviderError(message)

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
        import time as _time

        headers = self._concat_headers(options.headers) if options is not None else self.headers
        max_retries = self._config.max_retries
        base_delay = self._config.retry_base_delay

        for attempt in range(max_retries + 1):
            if not self._limiter.consume(1):
                # Local token bucket empty: sleep and retry (design sec 17),
                # giving up with RetriesExhaustedError when the budget is spent.
                retry_after = self._limiter.wait_time(1)
                logger.info(
                    "Massive local rate limit (attempt %d/%d), retrying in %.1fs",
                    attempt + 1,
                    max_retries + 1,
                    retry_after,
                )
                if attempt < max_retries:
                    _time.sleep(retry_after)
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
                translated: DataLibError = ConnectionError(f"Massive connection failed: {exc}")
                if attempt < max_retries:
                    delay = base_delay * (2**attempt)
                    logger.info(
                        "Massive connection failed (attempt %d/%d), retrying in %.1fs",
                        attempt + 1,
                        max_retries + 1,
                        delay,
                    )
                    _time.sleep(delay)
                    continue
                raise translated from exc

            if resp.status == 200:
                break

            translated = self._translate_response(resp)
            if isinstance(translated, (RateLimitError, ProviderError)):
                # 429 and 5xx are retryable; give up with RetriesExhaustedError
                # when the backoff budget is spent (design doc sec 17).
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
                    _time.sleep(retry_after)
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


class MassiveREST:
    """Thin wrapper around the official Massive REST client.

    Exposes only the endpoints DataKodo needs; raw rows are returned exactly as
    the provider defines them and normalization stays in the mapper module.
    """

    BASE_URL = "https://api.massive.com"

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
        self._client = _MassiveClient(
            api_key=self._massive.api_key,
            base=self._massive.base_url or self.BASE_URL,
            connect_timeout=self._massive.timeout,
            read_timeout=self._massive.timeout,
            config=self._config,
            limiter=self._limiter,
        )

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

        ``timeframe`` is a canonical DataKodo timeframe mapped to the Massive
        ``(multiplier, timespan)`` pair (design doc sec 19). ``adjust`` selects
        split-adjusted bars for equities (Massive default true); the flag is
        sent verbatim so the provider rejects it where it does not apply.

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
        for agg in self._client.list_aggs(
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

    # -- Reference / instrument discovery (design doc sec 5, 6.3) --

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
        """Fetch the Massive ticker reference, following ``next_url``.

        All filters are optional and combinable; ``market`` takes one of
        ``stocks | crypto | fx | indices | futures | options``. Each returned
        row is the raw record dict (``ticker``, ``name``, ``market``,
        ``primary_exchange``, ``type``, ``currency_name``, ``active``, ...).
        """
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
        tickers = self._client.list_tickers(
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

    def ticker_details(self, ticker: str) -> dict:
        """Fetch the single-ticker overview for ``ticker`` as a raw dict.

        Powers both instrument enrichment (options/futures typed extensions)
        and the fundamentals/reference capability (design doc sec 3).
        """
        logger.info("Fetching Massive ticker details for %s", ticker)
        details = self._client.get_ticker_details(ticker=ticker)
        return dict(vars(details)) if details else {}
