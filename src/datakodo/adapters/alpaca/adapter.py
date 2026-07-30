"""Alpaca adapter — implements the AdapterInterface for Alpaca Markets."""

import logging
from datetime import datetime

import pandas as pd

from datakodo.adapters.alpaca.mapper import map_ohlcv, map_trades
from datakodo.adapters.alpaca.rest import AlpacaREST
from datakodo.adapters.alpaca.ws import AlpacaWS
from datakodo.core.interfaces import AdapterInterface

logger = logging.getLogger(__name__)


class AlpacaAdapter(AdapterInterface):
    """Alpaca Markets adapter — equities and crypto.

    Capabilities: OHLCV, ticks (historical + streaming), streaming order book.
    Free tier available for delayed data.
    """

    supports_ohlcv = True
    supports_ticks = True
    supports_orderbook_snapshot = True
    supports_streaming_orderbook = True
    supports_streaming_ticks = True

    def __init__(self, api_key: str = "", api_secret: str = "") -> None:
        self._rest = AlpacaREST(api_key, api_secret)
        self._ws = AlpacaWS(api_key, api_secret)

    # -- historical (sync) --

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        raw = self._rest.bars(symbol, timeframe, start, end)
        return map_ohlcv(raw)

    # -- streaming (async) --

    async def stream_trades(self, symbol: str):
        async for raw in self._ws.trade_stream(symbol):
            yield map_trades(raw)

    async def stream_orderbook(self, symbol: str):
        async for raw in self._ws.orderbook_stream(symbol):
            yield raw
