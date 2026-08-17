"""MT5 adapter — implements the AdapterInterface for MetaTrader 5."""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from datakodo.adapters.mt5.config import MT5Config
from datakodo.adapters.mt5.mapper import MT5_OHLCV_EXTRAS, map_instrument, map_ohlcv
from datakodo.adapters.mt5.rest import MT5REST
from datakodo.adapters.mt5.terminal import MT5Terminal
from datakodo.core.config import Config
from datakodo.core.enums import AssetClass, Timeframe
from datakodo.core.exceptions import DataNotAvailableError, InvalidTimeframeError
from datakodo.core.instruments import Instrument
from datakodo.core.interfaces import AdapterInterface, symbol_of
from datakodo.core.schemas import resolve_ohlcv_columns
from datakodo.core.timeframe import MT5_MAP
from datakodo.ops.output import to_output_format
from datakodo.ops.validation import add_is_closed, validate_ohlcv

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

    concurrency_model = "serial"

    def __init__(
        self,
        terminal_path: str = "",
        *,
        config: Config | None = None,
        mt5_config: MT5Config | None = None,
    ) -> None:
        self._config = config or Config()
        self._terminal = MT5Terminal(terminal_path, mt5_config)
        self._rest = MT5REST(self._terminal)

    def connect(self) -> None:
        """Initialize the MT5 terminal connection."""
        self._terminal.initialize()

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
        self,
        symbol: str | Instrument,
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        columns: str | Sequence[str] = "basic",
        include_live: bool = False,
        output_format: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Fetch OHLCV candles for a date range (design doc sec 3/6/18).

        ``columns`` selects the schema: ``"basic"`` returns the canonical base
        columns; ``"all"`` adds every extra column MT5 returns and maps
        (``spread``, ``real_volume``, and ``session`` for forex); a list
        requests specific optional columns.

        By default only fully **closed** bars are returned (design doc sec 18);
        set ``include_live=True`` to keep the still-forming bar, marked
        ``is_closed=False``. An unknown ``timeframe`` raises
        ``InvalidTimeframeError``; an empty terminal history raises
        ``DataNotAvailableError``.

        ``output_format`` selects the user-facing representation (design doc
        sec 13): pandas (default), polars, or arrow — a per-call override of
        ``Config.output_format``.
        """
        symbol = symbol_of(symbol)
        mt5_tf = _to_mt5_timeframe(timeframe)
        raw = self._rest.copy_rates_range(symbol, mt5_tf, start, end)

        session_extras = self._session_extras(symbol) if _requests_session(columns) else ()
        available = MT5_OHLCV_EXTRAS + session_extras
        resolved = resolve_ohlcv_columns(columns, available)

        mapped_extras = tuple(extra for extra in MT5_OHLCV_EXTRAS if extra in resolved)
        df = map_ohlcv(
            raw,
            offset_seconds=self._rest.server_offset_seconds(symbol),
            extras=mapped_extras,
        )

        if df.empty:
            raise DataNotAvailableError(self._no_bars_message(symbol, timeframe, start, end))

        df = add_is_closed(df, timeframe)
        if not include_live:
            df = df.loc[df["is_closed"]].reset_index(drop=True)

        if df.empty:
            raise DataNotAvailableError(self._no_bars_message(symbol, timeframe, start, end))

        validate_ohlcv(df)
        logger.info("Fetched %d %s OHLCV rows for %s", len(df), timeframe, symbol)

        if "session" in resolved:
            df = df.assign(session="regular")
        return to_output_format(df[resolved], output_format or self._config.output_format)

    def _no_bars_message(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> str:
        return (
            f"No {timeframe} bars available for {symbol} "
            f"in [{start.isoformat()}, {end.isoformat()}]. "
            "Open the symbol's chart in MT5 (or raise 'Max. bars in chart')."
        )

    def _session_extras(self, symbol: str) -> tuple[str, ...]:
        """The ``session`` extra column for *symbol*, if any (design doc sec 9).

        ``session`` exists only for session-based asset classes. MT5's only
        such class is forex, so metals/CFDs/indices return no session extra.
        """
        info = self._rest.symbol_info(symbol)
        inst = map_instrument(
            symbol,
            info,
            futures_modes=self._rest.futures_calc_modes(),
            forex_modes=self._rest.forex_calc_modes(),
        )
        return ("session",) if inst.asset_class == AssetClass.FOREX else ()


def _to_mt5_timeframe(timeframe: str) -> int:
    """Map a canonical timeframe string to its MT5 constant.

    Raises ``InvalidTimeframeError`` for an unknown value.
    """
    try:
        return MT5_MAP[Timeframe(timeframe)]
    except ValueError:
        raise InvalidTimeframeError(f"Unknown timeframe: {timeframe!r}") from None


def _requests_session(columns: Any) -> bool:
    """True when the caller asked for the optional ``session`` column."""
    return columns == "all" or (isinstance(columns, (list, tuple, set)) and "session" in columns)
