"""Binance adapter: implements the AdapterInterface for Binance.

Supports both the spot and USD-M perpetual futures markets. OHLCV is
fetched for either market, validated, and optionally persisted to a
storage backend to form a simple ingestion pipeline.
"""

import logging
from datetime import UTC, datetime

import pandas as pd

from datakodo.adapters.binance.mapper import map_ohlcv, map_orderbook, map_ticks, map_trades
from datakodo.adapters.binance.rest import BinanceREST
from datakodo.adapters.binance.ws import BinanceWS
from datakodo.core.config import Config
from datakodo.core.enums import AssetClass, InstrumentType, Timeframe
from datakodo.core.exceptions import DataNotAvailableError
from datakodo.core.instruments import CryptoPerpetualExtension, Instrument
from datakodo.core.interfaces import AdapterInterface
from datakodo.core.schemas import OrderBook, Trade
from datakodo.core.timeframe import BINANCE_MAP
from datakodo.ops.pagination import paginate
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
                asset_class=AssetClass.CRYPTO,
                instrument_type=InstrumentType.PERPETUAL,
                exchange="Binance",
                currency=currency,
                crypto_perpetual=CryptoPerpetualExtension(contract_size=1.0),
            )
        return Instrument(
            symbol=symbol,
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
    ) -> pd.DataFrame:
        """Fetch OHLCV candles for a date range, validating and persisting them.

        ``market_type`` selects spot or USD-M futures klines; it defaults to the
        value configured on the adapter. By default only fully **closed** bars
        are returned (design doc sec 17/18): the still-forming last candle is
        excluded before validation. Set ``include_live=True`` to keep the open
        bar in the return value — though it is never written to cache. When
        ``persist`` is true (default comes from config), the closed bars are
        written to the configured storage backend under a deterministic cache key.
        """
        market_type = market_type or self._config.binance_market_type
        persist = self._config.cache_enabled if persist is None else persist
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
        logger.info("Validated %d OHLCV rows for %s %s", len(df), market_type, symbol)

        if persist:
            # Only closed/final data is ever cached (design doc sec 17).
            closed = df if not include_live else drop_incomplete_bars(df, timeframe)
            key = build_cache_key(
                f"binance-{market_type}", symbol, timeframe, (start.isoformat(), end.isoformat())
            )
            self._storage.write(key, closed)
            logger.info("Persisted OHLCV to cache key %s", key)
        return df

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
