"""IBKR adapter — implements the AdapterInterface for Interactive Brokers."""

import logging
from datetime import datetime

import pandas as pd

from datakodo.adapters.ibkr.client import IBKRClient
from datakodo.adapters.ibkr.mapper import map_ohlcv, map_trades
from datakodo.adapters.ibkr.ws import IBKRWS
from datakodo.core.interfaces import AdapterInterface

logger = logging.getLogger(__name__)


class IBKRAdapter(AdapterInterface):
    """Interactive Brokers adapter — equities, futures, options, bonds, forex.

    Uses the TWS/Gateway async callback-based API. Broadest single-provider
    asset coverage. Proves out the async/callback adapter pattern.
    """

    supports_ohlcv = True
    supports_ticks = True
    supports_orderbook_snapshot = True
    supports_streaming_orderbook = True
    supports_streaming_ticks = True
    supports_fundamentals = True

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1) -> None:
        self._client = IBKRClient(host, port, client_id)
        self._ws = IBKRWS(host, port, client_id)

    def connect(self) -> None:
        """Connect to TWS/Gateway."""
        self._client.connect()

    def disconnect(self) -> None:
        """Disconnect from TWS/Gateway."""
        self._client.disconnect()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()

    # -- historical (sync) --

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        raw = self._client.req_historical_data(symbol, timeframe, start, end)
        return map_ohlcv(raw)

    # -- streaming (async) --

    async def stream_trades(self, symbol: str):
        async for raw in self._ws.trade_stream(symbol):
            yield map_trades(raw)

    async def stream_orderbook(self, symbol: str):
        async for raw in self._ws.orderbook_stream(symbol):
            yield raw
