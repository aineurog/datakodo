"""MT5 adapter — implements the AdapterInterface for MetaTrader 5."""

import logging
from datetime import datetime

import pandas as pd

from datakodo.adapters.mt5.mapper import map_ohlcv
from datakodo.adapters.mt5.terminal import MT5Terminal
from datakodo.core.interfaces import AdapterInterface

logger = logging.getLogger(__name__)


class MT5Adapter(AdapterInterface):
    """MetaTrader 5 adapter — forex, CFDs, and metals.

    MT5's Python API is natively blocking (COM-based, Windows-only).
    This adapter runs via thread pool executor when needed.
    No streaming support — MT5 terminal doesn't provide a real-time
    tick feed via the Python API.
    """

    supports_ohlcv = True
    supports_ticks = False
    supports_orderbook_snapshot = False
    supports_streaming_orderbook = False
    supports_streaming_ticks = False

    def __init__(self, terminal_path: str = "") -> None:
        self._terminal = MT5Terminal(terminal_path)

    def connect(self) -> bool:
        """Initialize the MT5 terminal connection."""
        return self._terminal.initialize()

    def disconnect(self) -> None:
        """Shut down the MT5 terminal connection."""
        self._terminal.shutdown()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()

    # -- historical (sync) --

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        raw = self._terminal.copy_rates_range(symbol, timeframe, start, end)
        return map_ohlcv(raw)
