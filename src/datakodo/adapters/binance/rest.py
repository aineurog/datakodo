"""Binance REST client — wraps the ``python-binance`` library.

Request weights follow the official Binance docs (spot ``rest-api.md`` and
USD-M futures legacy docs) as of 2026:

  Spot klines 2 (flat); futures klines 1/2/5/10 by limit.
  Spot aggTrades 4 (flat); futures aggTrades 20 (flat).
  Spot depth 5/25/50/250; futures depth 2/5/10/20, by limit.

``ticks_all`` pages aggregate trades in one-hour windows (Binance caps the
aggTrades ``startTime``→``endTime`` span at 1 hour and futures history at
24 h) and dedupes by aggregate id.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException

from datakodo.core.config import Config
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

# One-hour cap on the aggTrades startTime/endTime span (spot + futures).
_AGGR_WINDOW_MS = 60 * 60 * 1000

# Valid order book depth limits per market.
_DEPTH_LIMITS: dict[str, tuple[int, ...]] = {
    "spot": (5, 10, 20, 50, 100, 500, 1000, 5000),
    "futures": (5, 10, 20, 50, 100, 500, 1000),
}


def _to_millis(dt: datetime) -> int:
    """Convert a (possibly naive) datetime to epoch milliseconds (UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _clamp_depth_limit(limit: int, market_type: str) -> int:
    """Clamp *limit* to the largest valid Binance depth value at most *limit*."""
    valid = _DEPTH_LIMITS.get(market_type, _DEPTH_LIMITS["spot"])
    for value in sorted(valid, reverse=True):
        if value <= limit:
            return value
    return valid[0]


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
        timeout: float | None = None,
        rate_limit: tuple[float, int] | None = None,
        config: Config | None = None,
    ) -> None:
        cfg = config or Config()
        if api_key:
            cfg = cfg.model_copy(
                update={
                    "binance_api_key": api_key,
                    "binance_api_secret": api_secret,
                }
            )
        self._config = cfg
        rate, burst = (
            rate_limit
            if rate_limit is not None
            else (
                cfg.binance_rate_limit_rate,
                cfg.binance_rate_limit_burst,
            )
        )
        self._limiter = TokenBucket(rate=rate, burst=burst)
        self._client = Client(
            cfg.binance_api_key,
            cfg.binance_api_secret,
            requests_params={"timeout": timeout if timeout is not None else cfg.binance_timeout},
            ping=False,
            tld=cfg.binance_tld,
            testnet=cfg.binance_testnet,
        )

    @staticmethod
    def _klines_weight(limit: int, market_type: str = "spot") -> int:
        """Binance kline request weight (official docs, 2026).

        Spot: flat 2. USD-M futures: 1 for [1,100), 2 for [100,500),
        5 for [500,1000], 10 above.
        """
        if market_type == "futures":
            if limit >= 1000:
                return 10
            if limit >= 500:
                return 5
            if limit >= 100:
                return 2
            return 1
        return 2

    @staticmethod
    def _aggregate_trades_weight(limit: int, market_type: str = "spot") -> int:
        """Binance aggregate-trades request weight (official docs, 2026).

        Spot: flat 4. USD-M futures: flat 20.
        """
        return 4 if market_type != "futures" else 20

    @staticmethod
    def _depth_weight(limit: int, market_type: str = "spot") -> int:
        """Binance order book depth request weight (official docs, 2026).

        Spot: 5 (1-100), 25 (101-500), 50 (501-1000), 250 (1001-5000).
        Futures: 2 (5-50), 5 (100), 10 (500), 20 (1000).
        """
        if market_type == "futures":
            if limit >= 1000:
                return 20
            if limit >= 500:
                return 10
            if limit >= 100:
                return 5
            return 2
        if limit >= 1001:
            return 250
        if limit >= 501:
            return 50
        if limit >= 101:
            return 25
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
        message = exc.message or exc.response.text

        if code == -1001:  # DISCONNECTED
            return ConnectionError(f"Binance connection lost: {message}")
        if code == -1121:  # INVALID_SYMBOL
            return SymbolNotFoundError(f"Binance symbol not found: {message}")
        if code in (-1022, -2014, -2015) or status in (401, 403):  # auth
            return AuthenticationError(f"Binance authentication failed: {message}")
        if code == -1003 or status in (418, 429):  # too many requests
            return RateLimitError(f"Binance rate limit: {message}")

        return ProviderError(f"Binance error ({code}): {message}", original=exc)

    def _call(self, caller: Any, weight: int, **params: Any) -> Any:
        """Run *caller* with rate limiting and exception mapping."""
        self._acquire(weight)
        try:
            return caller(**params)
        except BinanceAPIException as exc:
            raise self._translate(exc) from exc
        except BinanceRequestException as exc:
            raise ConnectionError(f"Binance request failed: {exc}") from exc

    def _market_caller(self, name: str, market_type: str) -> Any:
        """Pick the spot vs futures client method for ``name``.

        Spot methods are prefixed ``get_`` (``get_klines``, ...) while the
        USD-M futures methods are prefixed ``futures_`` (``futures_klines``, ...).
        """
        prefix = "futures_" if market_type == "futures" else "get_"
        return getattr(self._client, f"{prefix}{name}")

    def klines(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        limit: int = 1000,
        market_type: str = "spot",
    ) -> list:
        """Fetch raw Binance klines for ``symbol`` at ``interval``.

        One HTTP request for up to ``limit`` candles (Binance caps at 1000).
        ``start``/``end`` are optional; when given they are sent as
        ``startTime``/``endTime`` (epoch milliseconds). ``market_type`` selects
        the spot (``"spot"``) or USD-M futures (``"futures"``) klines endpoint.
        Returns the raw 12-field kline rows; the mapper converts them to
        canonical OHLCV. Both endpoints return the same row shape.
        """
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start is not None:
            params["startTime"] = _to_millis(start)
        if end is not None:
            params["endTime"] = _to_millis(end)
        logger.info(
            "Binance %s klines symbol=%s interval=%s start=%s end=%s limit=%s",
            market_type,
            symbol,
            interval,
            start,
            end,
            limit,
        )
        return list(
            self._call(
                self._market_caller("klines", market_type),
                self._klines_weight(limit, market_type),
                **params,
            )
        )

    def ticks(
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        limit: int = 1000,
        market_type: str = "spot",
    ) -> list:
        """Fetch up to ``limit`` raw aggregate trades in a single call.

        Use ``ticks_all`` for a full range — Binance caps aggTrades at 1000
        per call and requires the ``startTime``→``endTime`` span to be under
        one hour (futures history is limited to 24 h). Each returned record
        is an aggTrade with ``a`` (id), ``T`` (ts ms), ``p`` (price),
        ``q`` (qty), and ``m`` (buyer is maker).
        """
        page = max(1, min(limit, 1000))
        params: dict[str, Any] = {"symbol": symbol, "limit": page}
        if start is not None:
            params["startTime"] = _to_millis(start)
        if end is not None:
            params["endTime"] = _to_millis(end)
        logger.info(
            "Binance %s aggTrades symbol=%s start=%s end=%s limit=%s",
            market_type,
            symbol,
            start,
            end,
            page,
        )
        return list(
            self._call(
                self._market_caller("aggregate_trades", market_type),
                self._aggregate_trades_weight(page, market_type),
                **params,
            )
        )

    def ticks_all(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        *,
        limit: int = 1000,
        market_type: str = "spot",
    ) -> list:
        """Fetch every aggregate trade in ``start`` → ``end`` (paged).

        Binance caps aggTrades at 1000 per call and the ``startTime``/``endTime``
        span at one hour, so the range is paged in one-hour windows. The cursor
        advances past the last trade seen, draining windows that exceed one page,
        and results are deduplicated by aggregate id. Returns raw aggTrade rows.
        """
        if start >= end:
            raise ValueError(f"start ({start}) must be before end ({end}).")
        page = max(1, min(limit, 1000))
        start_ms = _to_millis(start)
        end_ms = _to_millis(end)
        rows: list[dict] = []
        seen: set[int] = set()
        cursor = start_ms

        while cursor <= end_ms:
            chunk_end = min(cursor + _AGGR_WINDOW_MS - 1, end_ms)
            params: dict[str, Any] = {
                "symbol": symbol,
                "limit": page,
                "startTime": cursor,
                "endTime": chunk_end,
            }
            logger.debug(
                "Binance %s aggTrades page symbol=%s [%s -> %s]",
                market_type,
                symbol,
                cursor,
                chunk_end,
            )
            data = list(
                self._call(
                    self._market_caller("aggregate_trades", market_type),
                    self._aggregate_trades_weight(page, market_type),
                    **params,
                )
            )
            for row in data:
                agg_id = int(row["a"])
                if agg_id not in seen:
                    seen.add(agg_id)
                    rows.append(row)

            if not data:
                cursor = chunk_end + 1
                continue
            # Full page -> more trades may exist within the window, advance by
            # the last trade seen; otherwise the window is exhausted.
            cursor = int(data[-1]["T"]) + 1 if len(data) >= page else chunk_end + 1

        return rows

    def orderbook(self, symbol: str, *, limit: int = 20, market_type: str = "spot") -> dict:
        """Fetch a raw Binance order book depth snapshot.

        Returns the depth dict with ``bids`` and ``asks`` lists of
        ``[price, quantity]`` rows. ``limit`` is clamped to Binance's supported
        depths (spot: 5/10/20/50/100/500/1000/5000; futures: 5..1000); 20 is the
        default.
        """
        depth_limit = _clamp_depth_limit(limit, market_type)
        params: dict[str, Any] = {"symbol": symbol, "limit": depth_limit}
        logger.info("Binance %s order book symbol=%s limit=%s", market_type, symbol, depth_limit)
        return dict(
            self._call(
                self._market_caller("order_book", market_type),
                self._depth_weight(depth_limit, market_type),
                **params,
            )
        )

    def ticker_24h(self, symbol: str, market_type: str = "spot") -> dict:
        """Fetch the raw Binance 24h rolling ticker for ``symbol``.

        Used as the fundamentals source. Spot returns a dict of price/volume
        stats; the USD-M futures endpoint returns a list with a single element,
        so both are normalized to a dict here.
        """
        params: dict[str, Any] = {"symbol": symbol}
        logger.info("Binance %s 24h ticker symbol=%s", market_type, symbol)
        result = self._call(
            self._market_caller("ticker", market_type),
            2,  # flat weight on both spot and USD-M futures
            **params,
        )
        return result if isinstance(result, dict) else result[0]

    def exchange_info(self, symbol: str, market_type: str = "spot") -> dict:
        """Fetch the raw Binance exchange info entry for one ``symbol``.

        Returns the dict describing the symbol: base/quote assets, trading
        status, permissions, and spot/margin trading flags. Both the spot and
        USD-M futures ``exchange_info`` endpoints return a ``symbols`` list
        covering every symbol, so the one matching ``symbol`` is picked out.
        """
        logger.info("Binance %s exchange info symbol=%s", market_type, symbol)
        result = self._call(
            self._market_caller("exchange_info", market_type),
            2,  # flat weight on both spot and USD-M futures
        )
        symbols = result.get("symbols") or []
        for entry in symbols:
            if entry.get("symbol") == symbol:
                return dict(entry)
        return {}  # symbol not present on this market
