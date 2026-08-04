"""Binance adapter: implements the AdapterInterface for Binance.

Supports both the spot and USD-M perpetual futures markets. OHLCV is
fetched for either market, validated, and optionally persisted to a
storage backend to form a simple ingestion pipeline.
"""

import logging
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from datakodo.adapters.binance.mapper import (
    map_fundamentals,
    map_ohlcv,
    map_orderbook,
    map_ticks,
    map_trades,
)
from datakodo.adapters.binance.rest import BinanceREST
from datakodo.adapters.binance.ws import BinanceWS
from datakodo.core.config import Config
from datakodo.core.enums import AssetClass, InstrumentType, Timeframe
from datakodo.core.exceptions import DataNotAvailableError
from datakodo.core.instruments import CryptoPerpetualExtension, Instrument
from datakodo.core.interfaces import AdapterInterface
from datakodo.core.schemas import Fundamentals, OrderBook, Trade
from datakodo.core.timeframe import BINANCE_MAP
from datakodo.ops.output import to_output_format
from datakodo.ops.pagination import paginate
from datakodo.ops.resample import pick_source_timeframe, resample
from datakodo.ops.validation import drop_incomplete_bars, validate_ohlcv
from datakodo.storage.cache import build_cache_key
from datakodo.storage.parquet import ParquetBackend

logger = logging.getLogger(__name__)


def _base_asset(symbol: str) -> str:
    """Best-effort quote asset for a symbol such as ``BTCUSDT``."""
    if symbol.endswith("USDT"):
        return "USDT"
    if symbol.endswith("BUSD"):
        return "BUSD"
    if symbol.endswith("USDC"):
        return "USDC"
    return symbol[-4:]


class BinanceAdapter(AdapterInterface):
    """Binance spot/perpetual futures market data adapter.

    Capabilities: OHLCV (spot + futures), ticks (historical + streaming),
    streaming order book. ``fetch_ohlcv`` validates the result and, when a
    storage backend is configured, persists it under a deterministic cache key.
    """

    supports_ohlcv = True
    supports_ticks = True
    supports_orderbook_snapshot = True
    supports_streaming_orderbook = True
    supports_streaming_ticks = True
    supports_fundamentals = True

    native_timeframes: tuple[Timeframe, ...] = tuple(Timeframe)
    """Binance offers every canonical timeframe natively, so no resampling is
    needed for Binance — the mechanism (design doc sec 7) still runs for
    adapters that restrict this list."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        storage: ParquetBackend | None = None,
        config: Config | None = None,
    ) -> None:
        self._config = config or Config()
        self._rest = BinanceREST(api_key, api_secret, config=self._config)
        self._ws = BinanceWS(api_key, api_secret, config=self._config)
        if storage is not None:
            self._storage = storage
        elif self._config.cache_enabled:
            self._storage = ParquetBackend(base_dir=str(self._config.cache_dir))
        else:
            self._storage = ParquetBackend(base_dir="")

    # -- instruments --

    def instrument(self, symbol: str, market_type: str = "spot") -> Instrument:
        """Build a canonical Instrument descriptor for ``symbol``.

        Perpetual futures are described with a ``CryptoPerpetualExtension``;
        spot pairs use a plain base ``Instrument``.
        """
        currency = _base_asset(symbol)
        if market_type == "futures":
            return Instrument(
                symbol=symbol,
                provider_symbol=symbol,
                asset_class=AssetClass.CRYPTO,
                instrument_type=InstrumentType.PERPETUAL,
                exchange="Binance",
                currency=currency,
                crypto_perpetual=CryptoPerpetualExtension(contract_size=1.0),
            )
        return Instrument(
            symbol=symbol,
            provider_symbol=symbol,
            asset_class=AssetClass.CRYPTO,
            instrument_type=InstrumentType.SPOT,
            exchange="Binance",
            currency=currency,
        )

    # -- historical (sync) --

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        market_type: str = "",
        persist: bool | None = None,
        include_live: bool = False,
        output_format: str | None = None,
        force_refresh: bool = False,
    ) -> Any:
        """Fetch OHLCV candles for a date range, validating and persisting them.

        ``market_type`` selects spot or USD-M futures klines; it defaults to the
        value configured on the adapter. By default only fully **closed** bars
        are returned (design doc sec 17/18): the still-forming last candle is
        excluded before validation. Set ``include_live=True`` to keep the open
        bar in the return value — though it is never written to cache. When
        ``persist`` is true (default comes from config), the closed bars are
        written to the configured storage backend under a deterministic cache key.

        If ``timeframe`` is not one of the adapter's ``native_timeframes``
        (design doc sec 7), the nearest smaller native timeframe is fetched and
        resampled up — controlled by ``Config.flag_resample`` for silent/flagged.
        Resampled output is always fully closed.

        ``output_format`` selects the user-facing representation (design doc
        sec 12): pandas (default), polars, arrow, or numpy — per-call override
        of ``Config.output_format``.

        ``force_refresh`` bypasses the cache read path and always hits the
        provider's API (design doc sec 17). The result is still persisted when
        ``persist`` is true.
        """
        market_type = market_type or self._config.binance_market_type
        persist = self._config.cache_enabled if persist is None else persist
        key = build_cache_key(
            f"binance-{market_type}", symbol, timeframe, (start.isoformat(), end.isoformat())
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
            df = self._fetch_ohlcv_native(symbol, timeframe, start, end, market_type, include_live)
        else:
            source_tf = pick_source_timeframe(tf, self.native_timeframes)
            self._log_resample(timeframe, source_tf.value)
            source = self._fetch_ohlcv_native(
                symbol, source_tf.value, start, end, market_type, include_live=False
            )
            df = resample(source, tf)
            validate_ohlcv(df)
            logger.info(
                "Resampled %s -> %s (%d bars) for %s %s",
                source_tf.value,
                timeframe,
                len(df),
                market_type,
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
        market_type: str,
        include_live: bool,
    ) -> pd.DataFrame:
        """Fetch ``timeframe`` candles that the provider offers natively.

        Shared by ``fetch_ohlcv`` for the direct path and as the source when
        resampling. Returns validated closed bars; ``include_live`` additionally
        keeps the still-forming bar in the return value only.
        """
        tf = Timeframe(timeframe)
        interval = BINANCE_MAP[tf]

        def _fetch_chunk(
            chunk_symbol: str, chunk_start: datetime, chunk_end: datetime
        ) -> pd.DataFrame:
            raw = self._rest.klines(
                chunk_symbol, interval, chunk_start, chunk_end, market_type=market_type
            )
            return map_ohlcv(raw)

        df = paginate(_fetch_chunk, symbol, tf, start, end)

        if not include_live:
            df = drop_incomplete_bars(df, timeframe)

        if df.empty:
            raise DataNotAvailableError(
                f"No closed {timeframe} bars available for {symbol} ({market_type}) "
                f"in [{start.isoformat()}, {end.isoformat()}]."
            )

        validate_ohlcv(df)
        logger.info("Fetched %d %s OHLCV rows for %s %s", len(df), timeframe, market_type, symbol)
        return df

    def _log_resample(self, requested: str, source: str) -> None:
        """Warn (or log quietly) that ``requested`` is derived by resampling."""
        if self._config.flag_resample:
            logger.warning(
                "%s has no native %s klines; fetching %s and resampling",
                self.__class__.__name__,
                requested,
                source,
            )
        else:
            logger.info("Deriving %s from %s by resampling", requested, source)

    def fetch_ticks(  # type: ignore[override]  # typed signature narrower than base
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        limit: int = 1000,
        market_type: str = "",
    ) -> list[Trade]:
        """Fetch historical trade ticks (aggTrade) mapped to canonical Trades.

        ``market_type`` defaults to the configured market. When a ``start`` is
        given the whole range is paged automatically (one-hour windows, deduped
        by aggregate id); without a ``start`` the most recent trades are fetched
        in a single call. Returns a list of canonical ``Trade`` records.
        """
        market_type = market_type or self._config.binance_market_type
        if start is not None:
            raw = self._rest.ticks_all(
                symbol,
                start,
                end if end is not None else datetime.now(UTC),
                limit=limit,
                market_type=market_type,
            )
        else:
            raw = self._rest.ticks(symbol, start, end, limit=limit, market_type=market_type)
        trades = map_ticks(raw)
        logger.info("Fetched %d %s trades for %s", len(trades), market_type, symbol)
        return trades

    def fetch_orderbook_snapshot(  # type: ignore[override]  # typed subset of base
        self,
        symbol: str,
        *,
        limit: int = 20,
        market_type: str = "",
    ) -> OrderBook:
        """Fetch a single canonical order book snapshot for ``symbol``."""
        market_type = market_type or self._config.binance_market_type
        raw = self._rest.orderbook(symbol, limit=limit, market_type=market_type)
        book = map_orderbook(raw)
        logger.info(
            "Fetched %s order book for %s (bids=%d asks=%d)",
            market_type,
            symbol,
            len(book.bids),
            len(book.asks),
        )
        return book

    def fetch_fundamentals(  # type: ignore[override]  # typed subset of base
        self,
        symbol: str,
        *,
        market_type: str = "",
    ) -> Fundamentals:
        """Fetch canonical fundamentals for ``symbol`` (design doc sec 3).

        Combines the Binance 24h rolling ticker (live price/volume stats) with
        exchange info (base/quote asset, trading status, permissions). Returns a
        canonical ``Fundamentals`` record with a ``CryptoFundamentals`` block.
        """
        market_type = market_type or self._config.binance_market_type
        ticker = self._rest.ticker_24h(symbol, market_type=market_type)
        info = self._rest.exchange_info(symbol, market_type=market_type)
        fundamentals = map_fundamentals(ticker, info)
        logger.info(
            "Fetched %s fundamentals for %s (latest=%s)",
            market_type,
            symbol,
            fundamentals.latest_price,
        )
        return fundamentals

    # -- streaming (async) --

    async def stream_trades(
        self, symbol: str, market_type: str = "spot", max_messages: int | None = None
    ):
        async for raw in self._ws.trade_stream(
            symbol, market_type=market_type, max_messages=max_messages
        ):
            yield map_trades(raw)

    async def stream_orderbook(
        self, symbol: str, market_type: str = "spot", max_messages: int | None = None
    ):
        async for raw in self._ws.orderbook_stream(
            symbol, market_type=market_type, max_messages=max_messages
        ):
            yield raw
