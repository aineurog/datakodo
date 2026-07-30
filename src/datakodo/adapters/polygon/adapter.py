"""Polygon adapter — implements the AdapterInterface for Polygon.io."""

import logging
from datetime import datetime

import pandas as pd

from datakodo.adapters.polygon.mapper import map_ohlcv, map_trades
from datakodo.adapters.polygon.rest import PolygonREST
from datakodo.adapters.polygon.ws import PolygonWS
from datakodo.core.interfaces import AdapterInterface

logger = logging.getLogger(__name__)


class PolygonAdapter(AdapterInterface):
    """Polygon.io adapter — equities, forex, and crypto.

    Capabilities: OHLCV, ticks (historical + streaming), reference/fundamentals.
    Strong for reference data.
    """

    supports_ohlcv = True
    supports_ticks = True
    supports_orderbook_snapshot = False
    supports_streaming_orderbook = False
    supports_streaming_ticks = True
    supports_fundamentals = True

    def __init__(self, api_key: str = "") -> None:
        self._rest = PolygonREST(api_key)
        self._ws = PolygonWS(api_key)

    # -- historical (sync) --

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        raw = self._rest.aggs(symbol, timeframe, start, end)
        return map_ohlcv(raw)

    # -- streaming (async) --

    async def stream_trades(self, symbol: str):
        async for raw in self._ws.trade_stream(symbol):
            yield map_trades(raw)
