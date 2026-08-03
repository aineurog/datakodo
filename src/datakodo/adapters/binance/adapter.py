"""Binance adapter: implements the AdapterInterface for Binance.

Supports both the spot and USD-M perpetual futures markets. OHLCV is
fetched for either market, validated, and optionally persisted to a
storage backend to form a simple ingestion pipeline.
"""

import logging
from datetime import datetime

import pandas as pd

from datakodo.adapters.binance.mapper import map_ohlcv, map_trades
from datakodo.adapters.binance.rest import BinanceREST
from datakodo.adapters.binance.ws import BinanceWS
from datakodo.core.enums import AssetClass, InstrumentType, Timeframe
from datakodo.core.instruments import CryptoPerpetualExtension, Instrument
from datakodo.core.interfaces import AdapterInterface
from datakodo.core.timeframe import BINANCE_MAP
from datakodo.ops.pagination import paginate
from datakodo.ops.validation import validate_ohlcv
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
    ) -> None:
        self._rest = BinanceREST(api_key, api_secret)
        self._ws = BinanceWS(api_key, api_secret)
        self._storage = storage or ParquetBackend()

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
        market_type: str = "spot",
        persist: bool = True,
    ) -> pd.DataFrame:
        """Fetch OHLCV candles for a date range, validating and persisting them.

        ``market_type`` selects spot or USD-M futures klines. When ``persist``
        is true the validated frame is written to the configured storage
        backend under a deterministic cache key.
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
        validate_ohlcv(df)
        logger.info("Validated %d OHLCV rows for %s %s", len(df), market_type, symbol)

        if persist:
            key = build_cache_key(
                f"binance-{market_type}", symbol, timeframe, (start.isoformat(), end.isoformat())
            )
            self._storage.write(key, df)
            logger.info("Persisted OHLCV to cache key %s", key)
        return df

    # -- streaming (async) --

    async def stream_trades(self, symbol: str):
        async for raw in self._ws.trade_stream(symbol):
            yield map_trades(raw)

    async def stream_orderbook(self, symbol: str):
        async for raw in self._ws.orderbook_stream(symbol):
            yield raw
