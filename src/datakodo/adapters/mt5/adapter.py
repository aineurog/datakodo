"""MT5 adapter - implements the AdapterInterface for MetaTrader 5."""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from datakodo.adapters.mt5.config import MT5Config
from datakodo.adapters.mt5.mapper import (
    MT5_OHLCV_EXTRAS,
    map_fundamentals,
    map_instrument,
    map_ohlcv,
)
from datakodo.adapters.mt5.rest import MT5REST
from datakodo.adapters.mt5.terminal import MT5Terminal
from datakodo.core.config import Config
from datakodo.core.enums import AssetClass, Timeframe
from datakodo.core.exceptions import (
    DataNotAvailableError,
    InvalidTimeframeError,
    ProviderError,
    SymbolNotFoundError,
)
from datakodo.core.instruments import Instrument
from datakodo.core.interfaces import AdapterInterface, symbol_of
from datakodo.core.schemas import Fundamentals, resolve_ohlcv_columns
from datakodo.core.timeframe import MT5_MAP
from datakodo.ops.output import to_output_format
from datakodo.ops.pagination import paginate
from datakodo.ops.resample import pick_source_timeframe, resample
from datakodo.ops.validation import add_is_closed, detect_gaps, validate_ohlcv

logger = logging.getLogger(__name__)


class MT5Adapter(AdapterInterface):
    """MetaTrader 5 adapter - forex, CFDs, and metals.

    MT5's Python API is natively blocking (COM-based, Windows-only).
    This adapter runs via thread pool executor when needed.
    No streaming support - MT5 terminal doesn't provide a real-time
    tick feed via the Python API.
    """

    supports_ohlcv = True
    supports_ticks = False
    supports_orderbook_snapshot = False
    supports_streaming_orderbook = False
    supports_streaming_ticks = False
    supports_fundamentals = True

    concurrency_model = "serial"

    native_timeframes: tuple[Timeframe, ...] = tuple(Timeframe)
    """MT5 offers every canonical timeframe natively (``TIMEFRAME_M1..MN1``),
    so resampling never triggers in practice - the mechanism (design doc sec 8)
    still runs for adapters that restrict this list."""

    def __init__(
        self,
        terminal_path: str = "",
        *,
        config: Config | None = None,
        mt5_config: MT5Config | None = None,
    ) -> None:
        self._config = config or Config()
        self._terminal = MT5Terminal(terminal_path, mt5_config)
        self._rest = MT5REST(self._terminal, config=self._config)

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

    # -- instruments (design doc sec 4/5) --

    def instrument(self, symbol: str, market_type: str = "") -> Instrument:
        """Classify ``symbol`` as spot, futures, forex, CFD, etc.

        MT5 symbols are self-describing: ``symbol_info`` returns the broker's
        symbol metadata (``trade_calc_mode``, Market Watch ``path``, contract
        sizes, expiry). ``market_type`` is an optional hint (``"spot"`` /
        ``"futures"``) that is validated against the detected classification -
        a mismatch raises ``ProviderError``. An unknown symbol raises
        ``SymbolNotFoundError`` (design doc sec 16).
        """
        self._rest.ensure_symbol_known(symbol)
        info = self._rest.symbol_info(symbol)
        if info is None:
            raise SymbolNotFoundError(f"Symbol {symbol!r} has no info on MT5.")
        return map_instrument(
            symbol,
            info,
            futures_modes=self._rest.futures_calc_modes(),
            forex_modes=self._rest.forex_calc_modes(),
            market_type=market_type,
        )

    def search_instruments(
        self,
        query: str = "",
        *,
        asset_class: Any = None,
        instrument_type: Any = None,
        quote: str | None = None,
        exchange: str | None = None,
        limit: int = 100,
        **kwargs: Any,
    ) -> list[Instrument]:
        """Search the terminal's full symbol universe (design doc sec 5).

        ``mt5.symbols_get()`` returns every symbol the broker serves in a
        single local call - MT5's cheap symbol list. Each entry is classified
        into a canonical ``Instrument`` via ``map_instrument`` (spot vs
        futures, forex, CFD, metal, ...), then filtered client-side.
        ``query`` is a case-insensitive substring of the symbol (or the
        futures underlying, when present); ``asset_class``,
        ``instrument_type``, ``quote`` (the quote/profit currency), and
        ``exchange`` are optional and combinable. Requires a connected
        terminal.
        """
        futures_modes = self._rest.futures_calc_modes()
        forex_modes = self._rest.forex_calc_modes()
        results: list[Instrument] = []
        for entry in self._rest.symbols_get() or []:
            symbol = getattr(entry, "name", "") or ""
            if not symbol:
                continue
            try:
                inst = map_instrument(
                    symbol,
                    entry,
                    futures_modes=futures_modes,
                    forex_modes=forex_modes,
                )
            except ProviderError:
                continue
            if not self._search_match(inst, query, asset_class, instrument_type, quote, exchange):
                continue
            results.append(inst)
            if len(results) >= limit:
                break
        logger.info("MT5 search returned %d instruments", len(results))
        return results

    def _search_match(
        self,
        inst: Instrument,
        query: str,
        asset_class: Any,
        instrument_type: Any,
        quote: str | None,
        exchange: str | None,
    ) -> bool:
        """Apply one symbol against every optional search filter (sec 5)."""
        if query:
            names = [inst.symbol]
            if inst.future is not None and inst.future.underlying:
                names.append(inst.future.underlying)
            if not any(query.lower() in name.lower() for name in names):
                return False
        if asset_class is not None and inst.asset_class != asset_class:
            return False
        if instrument_type is not None and inst.instrument_type != instrument_type:
            return False
        if quote is not None and inst.currency.lower() != quote.lower():
            return False
        if exchange is not None and inst.exchange.lower() != exchange.lower():
            return False
        return True

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

        A timeframe outside ``native_timeframes`` (design doc sec 8) is derived
        by fetching the largest smaller native timeframe and resampling up,
        controlled by ``Config.flag_resample`` for silent/flagged. MT5 offers
        every canonical timeframe natively, so this never triggers in practice
        for a stock ``MT5Adapter`` - it applies to restricted subclasses.

        ``output_format`` selects the user-facing representation (design doc
        sec 13): pandas (default), polars, or arrow - a per-call override of
        ``Config.output_format``.

        Ranges wider than ``MT5Config.max_bars`` bars are auto-paginated and
        stitched by ``ops.pagination.paginate`` (design doc sec 12), so a
        single call transparently covers ranges larger than the terminal's
        loaded chart history.
        """
        symbol = symbol_of(symbol)
        mt5_tf = _to_mt5_timeframe(timeframe)
        tf = Timeframe(timeframe)
        # MT5 only buffers history for symbols visible in MarketWatch. Selecting the
        # symbol first forces the terminal to download/load its bars (a symbol
        # whose chart was never opened still returns data), and an unknown
        # symbol fails the select -> SymbolNotFoundError.
        self._rest.ensure_symbol_known(symbol)
        offset_seconds = self._rest.server_offset_seconds(symbol)

        if start >= end:
            raise DataNotAvailableError(self._no_bars_message(symbol, timeframe, start, end))

        if tf in self.native_timeframes:
            df, available = self._fetch_ohlcv_native(
                symbol,
                tf,
                mt5_tf,
                start,
                end,
                offset_seconds=offset_seconds,
                include_live=include_live,
                columns=columns,
            )
        else:
            source_tf = pick_source_timeframe(tf, self.native_timeframes)
            self._log_resample(timeframe, source_tf.value)
            source, _ = self._fetch_ohlcv_native(
                symbol,
                source_tf,
                MT5_MAP[source_tf],
                start,
                end,
                offset_seconds=offset_seconds,
                include_live=False,
                columns="basic",
            )
            df = resample(source, tf)
            validate_ohlcv(df)
            self._log_gaps(symbol, timeframe, start, end, df)
            available = ()
            logger.info(
                "Resampled %s -> %s (%d bars) for %s",
                source_tf.value,
                timeframe,
                len(df),
                symbol,
            )

        resolved = resolve_ohlcv_columns(columns, available)
        if "session" in resolved:
            df = df.assign(session="regular")
        return to_output_format(df[resolved], output_format or self._config.output_format)

    def _fetch_ohlcv_native(
        self,
        symbol: str,
        tf: Timeframe,
        mt5_tf: int,
        start: datetime,
        end: datetime,
        *,
        offset_seconds: int,
        include_live: bool,
        columns: str | Sequence[str],
    ) -> tuple[Any, tuple[str, ...]]:
        """Fetch ``timeframe`` bars the terminal offers natively.

        Shared by ``fetch_ohlcv`` for the direct path and as the source when
        resampling. Returns ``(validated_bars, available_extras)`` with an
        ``is_closed`` column; ``include_live`` keeps the still-forming bar
        (marked ``is_closed=False``), otherwise only closed bars are returned.
        """
        timeframe = tf.value
        session_extras = self._session_extras(symbol) if _requests_session(columns) else ()
        available = MT5_OHLCV_EXTRAS + session_extras
        resolved = resolve_ohlcv_columns(columns, available)
        mapped_extras = tuple(extra for extra in MT5_OHLCV_EXTRAS if extra in resolved)

        def _fetch_chunk(chunk_symbol: str, chunk_start: datetime, chunk_end: datetime) -> Any:
            raw = self._rest.copy_rates_range(
                chunk_symbol,
                mt5_tf,
                chunk_start,
                chunk_end,
                offset_seconds=offset_seconds,
            )
            return map_ohlcv(raw, offset_seconds=offset_seconds, extras=mapped_extras)

        df = paginate(
            _fetch_chunk,
            symbol,
            tf,
            start,
            end,
            max_per_request=self._terminal.config.max_bars,
        )

        if df.empty:
            raise DataNotAvailableError(self._no_bars_message(symbol, timeframe, start, end))

        df = add_is_closed(df, timeframe)
        if not include_live:
            df = df.loc[df["is_closed"]].reset_index(drop=True)

        if df.empty:
            raise DataNotAvailableError(self._no_bars_message(symbol, timeframe, start, end))

        validate_ohlcv(df)
        self._log_gaps(symbol, timeframe, start, end, df)
        logger.info("Fetched %d %s OHLCV rows for %s", len(df), timeframe, symbol)
        return df, available

    def _log_gaps(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, df: Any
    ) -> None:
        """Warn when the fetched frame has missing candles (design doc sec 18)."""
        gaps = detect_gaps(df, timeframe)
        if not gaps.empty:
            missing = int(gaps["gap_missing"].sum())
            logger.warning(
                "Gap detected in %s %s [%s \u2192 %s]: %d gap(s), %d missing candle(s)",
                symbol,
                timeframe,
                start.isoformat(),
                end.isoformat(),
                len(gaps),
                missing,
            )

    def _log_resample(self, requested: str, source: str) -> None:
        """Warn (or log quietly) that ``requested`` is derived by resampling."""
        if self._config.flag_resample:
            logger.warning(
                "%s has no native %s bars; fetching %s and resampling",
                self.__class__.__name__,
                requested,
                source,
            )
        else:
            logger.info("Deriving %s from %s by resampling", requested, source)

    def fetch_fundamentals(  # type: ignore[override]  # typed subset of base
        self,
        symbol: str,
    ) -> Fundamentals:
        """Fetch canonical fundamentals / reference data for ``symbol``.

        Combines ``symbol_info`` (currencies, description, classification) with
        the latest ``Tick`` (live quote time) and the server-time offset, so
        ``as_of`` is returned in true UTC (design doc sec 3/10). An unknown
        symbol raises ``SymbolNotFoundError`` (design doc sec 16).
        """
        self._rest.ensure_symbol_known(symbol)
        info = self._rest.symbol_info(symbol)
        if info is None:
            raise SymbolNotFoundError(f"Symbol {symbol!r} has no info on MT5.")
        tick = self._rest.symbol_info_tick(symbol)
        offset_seconds = self._rest.server_offset_seconds(symbol)
        fundamentals = map_fundamentals(
            symbol,
            info,
            tick=tick,
            futures_modes=self._rest.futures_calc_modes(),
            forex_modes=self._rest.forex_calc_modes(),
            offset_seconds=offset_seconds,
        )
        logger.info("Fetched MT5 fundamentals for %s (as_of=%s)", symbol, fundamentals.as_of)
        return fundamentals

    def _no_bars_message(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> str:
        return (
            f"No {timeframe} bars available for {symbol} "
            f"in [{start.isoformat()}, {end.isoformat()}] yet. "
            "Data is downloading in the background - this may take some time. "
            "Open the symbol's chart in MT5 (or raise 'Max. bars in chart') "
            "to speed it up."
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
