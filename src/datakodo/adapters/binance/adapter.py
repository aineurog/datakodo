"""Binance adapter — implements the AdapterInterface for Binance."""

import logging
from datetime import datetime

import pandas as pd

from datakodo.adapters.binance.mapper import map_ohlcv, map_trades
from datakodo.adapters.binance.rest import BinanceREST
from datakodo.adapters.binance.ws import BinanceWS
from datakodo.core.enums import Timeframe
from datakodo.core.interfaces import AdapterInterface
from datakodo.core.timeframe import BINANCE_MAP
from datakodo.ops.pagination import paginate

logger = logging.getLogger(__name__)


class BinanceAdapter(AdapterInterface):
    """Binance spot/perpetual futures market data adapter.

    Capabilities: OHLCV, ticks (historical + streaming), streaming order book.
    """

    supports_ohlcv = True
    supports_ticks = True
    supports_orderbook_snapshot = True
    supports_streaming_orderbook = True
    supports_streaming_ticks = True

    def __init__(self, api_key: str = "", api_secret: str = "") -> None:
        self._rest = BinanceREST(api_key, api_secret)
        self._ws = BinanceWS()

    # -- historical (sync) --

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        tf = Timeframe(timeframe)
        interval = BINANCE_MAP[tf]

        def _fetch_chunk(symbol: str, chunk_start: datetime, chunk_end: datetime) -> pd.DataFrame:
            raw = self._rest.klines(symbol, interval, chunk_start, chunk_end)
            return map_ohlcv(raw)

        return paginate(_fetch_chunk, symbol, tf, start, end)

    # -- streaming (async) --

    async def stream_trades(self, symbol: str):
        async for raw in self._ws.trade_stream(symbol):
            yield map_trades(raw)

    async def stream_orderbook(self, symbol: str):
        async for raw in self._ws.orderbook_stream(symbol):
            yield raw
