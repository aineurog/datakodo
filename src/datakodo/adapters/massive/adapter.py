"""Massive adapter — implements the AdapterInterface for Massive.com."""

import logging
from datetime import datetime

import pandas as pd

from datakodo.adapters.massive.mapper import map_ohlcv, map_trades
from datakodo.adapters.massive.rest import MassiveREST
from datakodo.adapters.massive.ws import MassiveWS
from datakodo.core.instruments import Instrument
from datakodo.core.interfaces import AdapterInterface, symbol_of

logger = logging.getLogger(__name__)


class MassiveAdapter(AdapterInterface):
    """Massive.com adapter — equities, forex, and crypto.

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
        self,
        symbol: str | Instrument,
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        columns="basic",
        **kwargs,
    ) -> pd.DataFrame:
        symbol = symbol_of(symbol)
        raw = self._rest.aggs(symbol, timeframe, start, end)
        return map_ohlcv(raw)

    # -- streaming (async) --

    async def stream_trades(self, symbol: str | Instrument):
        symbol = symbol_of(symbol)
        async for raw in self._ws.trade_stream(symbol):
            yield map_trades(raw)
