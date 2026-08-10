"""Massive adapter — implements the AdapterInterface for Massive.com.

Massive (formerly Polygon.io) rebranded on 2025-10-30; the API base moved from
``api.polygon.io`` to ``api.massive.com``.  Existing API keys keep working.
"""

import logging
from datetime import datetime

import pandas as pd

from datakodo.adapters.massive.mapper import map_ohlcv, map_trades
from datakodo.adapters.massive.rest import MassiveREST
from datakodo.adapters.massive.ws import MassiveWS
from datakodo.core.interfaces import AdapterInterface

logger = logging.getLogger(__name__)


class MassiveAdapter(AdapterInterface):
    """Massive.com adapter — equities, forex, crypto, options, indices, futures.

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
        self._rest = MassiveREST(api_key)
        self._ws = MassiveWS(api_key)

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
