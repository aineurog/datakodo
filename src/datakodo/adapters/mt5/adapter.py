"""MT5 adapter — implements the AdapterInterface for MetaTrader 5."""

import logging
from datetime import datetime

import pandas as pd

from datakodo.adapters.mt5.mapper import map_ohlcv
from datakodo.adapters.mt5.terminal import MT5Terminal
from datakodo.core.enums import Timeframe
from datakodo.core.exceptions import InvalidTimeframeError
from datakodo.core.interfaces import AdapterInterface
from datakodo.core.timeframe import MT5_MAP
from datakodo.ops.validation import detect_gaps, drop_incomplete_bars, validate_ohlcv

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

    def connect(self) -> None:
        """Initialize the MT5 terminal connection."""
        self._terminal.initialize()

    def disconnect(self) -> None:
        """Shut down the MT5 terminal connection."""
        self._terminal.shutdown()

    # -- historical (sync) --

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        include_live: bool = False,
    ) -> pd.DataFrame:
        """Fetch OHLCV bars for a date range, validated and gap-checked.

        By default only fully **closed** bars are returned (design doc
        sec 17): the last, still-forming bar is excluded before validation.
        Set ``include_live=True`` to keep the open (still-forming) bar in
        the return value — mirroring ``BinanceAdapter.fetch_ohlcv``.
        """
        try:
            tf: int = MT5_MAP[Timeframe(timeframe)]
        except (KeyError, ValueError) as exc:
            raise InvalidTimeframeError(f"Unknown MT5 timeframe: {timeframe!r}") from exc
        raw = self._terminal.copy_rates_range(symbol, tf, start, end)
        if raw is None:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume", "session"]
            )
        offset = self._terminal.server_offset_seconds(symbol)
        df = map_ohlcv(raw, offset_seconds=offset)
        if df.empty:
            return df
        if not include_live:
            df = drop_incomplete_bars(df, timeframe)
        validate_ohlcv(df)
        gaps = detect_gaps(df, timeframe)
        if not gaps.empty:
            missing = int(gaps["gap_missing"].sum())
            logger.warning(
                "Gap detected in %s %s [%s → %s]: %d gap(s), %d missing candle(s)",
                symbol,
                timeframe,
                start.isoformat(),
                end.isoformat(),
                len(gaps),
                missing,
            )
        return df
