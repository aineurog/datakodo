"""Massive adapter — implements the AdapterInterface for Massive.com.

Massive (formerly Polygon.io) rebranded on 2025-10-30; the API base moved from
``api.polygon.io`` to ``api.massive.com``.  Existing API keys keep working.
"""

import logging
from datetime import datetime
from typing import Any

import pandas as pd

from datakodo.adapters.massive.mapper import map_ohlcv, map_ticks, map_trades
from datakodo.adapters.massive.rest import MassiveREST
from datakodo.adapters.massive.ws import MassiveWS
from datakodo.core.config import Config
from datakodo.core.enums import Timeframe
from datakodo.core.exceptions import DataNotAvailableError
from datakodo.core.interfaces import AdapterInterface
from datakodo.core.schemas import Trade
from datakodo.core.timeframe import MASSIVE_TIMESPANS
from datakodo.ops.output import to_output_format
from datakodo.ops.resample import pick_source_timeframe, resample
from datakodo.ops.validation import drop_incomplete_bars, validate_ohlcv
from datakodo.storage.cache import build_cache_key
from datakodo.storage.parquet import ParquetBackend

logger = logging.getLogger(__name__)


class MassiveAdapter(AdapterInterface):
    """Massive.com adapter — equities, forex, crypto, options, indices, futures.

    Capabilities: OHLCV, ticks (historical + streaming), reference/fundamentals.
    Strong for reference data.

    Massive addresses every asset class through the same aggregates endpoint;
    the ticker prefix (e.g. ``X:BTCUSD``, ``C:EURUSD``) selects the market.
    """

    supports_ohlcv = True
    supports_ticks = True
    supports_orderbook_snapshot = False
    supports_streaming_orderbook = False
    supports_streaming_ticks = True
    supports_fundamentals = True

    native_timeframes: tuple[Timeframe, ...] = tuple(Timeframe)
    """Massive offers minute/hour/day/week/month bars natively (multiplier x
    timespan), covering every canonical timeframe, so no resampling is needed."""

    def __init__(
        self,
        api_key: str = "",
        storage: ParquetBackend | None = None,
        config: Config | None = None,
    ) -> None:
        self._config = config or Config()
        self._rest = MassiveREST(api_key, config=self._config)
        self._ws = MassiveWS(api_key)
        if storage is not None:
            self._storage = storage
        elif self._config.cache_enabled:
            self._storage = ParquetBackend(base_dir=str(self._config.cache_dir))
        else:
            self._storage = ParquetBackend(base_dir="")

    # -- historical (sync) --

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        persist: bool | None = None,
        include_live: bool = False,
        output_format: str | None = None,
        force_refresh: bool = False,
    ) -> Any:
        """Fetch OHLCV bars for a date range, validating and persisting them.

        By default only fully **closed** bars are returned (design doc sec
        17/18): the still-forming last bar is excluded before validation. Set
        ``include_live=True`` to keep the open bar in the return value —
        though it is never written to cache. When ``persist`` is true (default
        comes from config), the closed bars are written to the configured
        storage backend under a deterministic cache key.

        If ``timeframe`` is not one of the adapter's ``native_timeframes``
        (design doc sec 7), the nearest smaller native timeframe is fetched
        and resampled up — controlled by ``Config.flag_resample`` for
        silent/flagged logging. Resampled output is always fully closed.

        ``output_format`` selects the user-facing representation (design doc
        sec 12): pandas (default), polars, arrow, or numpy — per-call override
        of ``Config.output_format``.

        ``force_refresh`` bypasses the cache read path and always hits the
        provider's API (design doc sec 17). The result is still persisted when
        ``persist`` is true.
        """
        persist = self._config.cache_enabled if persist is None else persist
        key = build_cache_key(
            "massive", symbol, timeframe, (start.isoformat(), end.isoformat())
        )
        tf = Timeframe(timeframe)

        # Cache hit: return persisted data without touching the provider
        # (design doc sec 17 — avoid re-fetching immutable historical data).
        if persist and not force_refresh and not include_live and tf in self.native_timeframes:
            cached = self._try_cache_read(key)
            if cached is not None:
                logger.info("Cache hit for %s (%d rows)", key, len(cached))
                return to_output_format(cached, output_format or self._config.output_format)

        if tf in self.native_timeframes:
            df = self._fetch_ohlcv_native(symbol, timeframe, start, end, include_live)
        else:
            source_tf = pick_source_timeframe(tf, self.native_timeframes)
            self._log_resample(timeframe, source_tf.value)
            source = self._fetch_ohlcv_native(
                symbol, source_tf.value, start, end, include_live=False
            )
            df = resample(source, tf)
            validate_ohlcv(df)
            logger.info(
                "Resampled %s -> %s (%d bars) for %s",
                source_tf.value,
                timeframe,
                len(df),
                symbol,
            )

        if persist:
            # Only closed/final data is ever cached (design doc sec 17).
            closed = df if not include_live else drop_incomplete_bars(df, timeframe)
            self._storage.write(key, closed)
            logger.info("Persisted OHLCV to cache key %s", key)
        return to_output_format(df, output_format or self._config.output_format)

    def _try_cache_read(self, key: str) -> pd.DataFrame | None:
        """Return cached OHLCV data for *key* if it exists, else None."""
        try:
            if self._storage.exists(key):
                df = pd.DataFrame(self._storage.read(key))
                if not df.empty and "timestamp" in df.columns:
                    return df
        except (KeyError, TypeError):
            pass
        return None

    def _fetch_ohlcv_native(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        include_live: bool,
    ) -> pd.DataFrame:
        """Fetch ``timeframe`` bars that the provider offers natively.

        Shared by ``fetch_ohlcv`` for the direct path and as the source when
        resampling. Returns validated closed bars; ``include_live`` additionally
        keeps the still-forming bar in the return value only.
        """
        multiplier, timespan = MASSIVE_TIMESPANS[Timeframe(timeframe)]
        raw = self._rest.aggs(symbol, multiplier, timespan, start, end)
        df = map_ohlcv(raw)

        if not include_live:
            df = drop_incomplete_bars(df, timeframe)

        if df.empty:
            raise DataNotAvailableError(
                f"No closed {timeframe} bars available for {symbol} "
                f"in [{start.isoformat()}, {end.isoformat()}]."
            )

        validate_ohlcv(df)
        logger.info("Fetched %d %s OHLCV rows for %s", len(df), timeframe, symbol)
        return df

    def _log_resample(self, requested: str, source: str) -> None:
        """Warn (or log quietly) that ``requested`` is derived by resampling."""
        if self._config.flag_resample:
            logger.warning(
                "%s has no native %s bars; fetching %s and resampling",
                self.__class__.__name__,
                requested,
                source,
            )
        else:
            logger.info("Deriving %s from %s by resampling", requested, source)

    def fetch_futures_ohlcv(
        self,
        symbol: str,
        resolution: str,
        start: datetime,
        end: datetime,
        *,
        limit: int = 50000,
    ) -> pd.DataFrame:
        """Fetch OHLCV bars for a futures contract (``/futures/v1/aggs``).

        Massive futures use a ``resolution`` string (``1min``, ``1session``,
        ...) instead of the multiplier/timespan pair. Returns the canonical
        OHLCV DataFrame.
        """
        raw = self._rest.futures_aggs(symbol, resolution, start, end, limit=limit)
        return map_ohlcv(raw)

    def fetch_ticks(  # type: ignore[override]  # typed signature narrower than base
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        limit: int = 50000,
    ) -> list[Trade]:
        """Fetch historical trade ticks mapped to canonical Trades.

        ``start``/``end`` are optional; when given the v3 trades endpoint is
        filtered to that range (nanosecond precision). Returns a list of
        canonical ``Trade`` records.
        """
        raw = self._rest.trades(symbol, start, end, limit=limit)
        ticks = map_ticks(raw)
        logger.info("Fetched %d trades for %s", len(ticks), symbol)
        return ticks

    # -- streaming (async) --

    async def stream_trades(self, symbol: str):
        async for raw in self._ws.trade_stream(symbol):
            yield map_trades(raw)
