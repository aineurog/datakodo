"""MT5 adapter — implements the AdapterInterface for MetaTrader 5."""

import logging
from datetime import datetime

import pandas as pd

from datakodo.adapters.mt5.mapper import map_fundamentals, map_instrument, map_ohlcv
from datakodo.adapters.mt5.terminal import MT5Terminal
from datakodo.core.config import Config
from datakodo.core.enums import Timeframe
from datakodo.core.exceptions import InvalidTimeframeError, ProviderError
from datakodo.core.instruments import Instrument
from datakodo.core.interfaces import AdapterInterface
from datakodo.core.schemas import Fundamentals
from datakodo.core.timeframe import MT5_MAP
from datakodo.ops.output import to_output_format
from datakodo.ops.pagination import paginate
from datakodo.ops.resample import pick_source_timeframe, resample
from datakodo.ops.validation import detect_gaps, drop_incomplete_bars, validate_ohlcv
from datakodo.storage.cache import build_cache_key
from datakodo.storage.parquet import ParquetBackend

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
    supports_fundamentals = True

    # MT5 offers every canonical timeframe natively (TIMEFRAME_M1..MN1), so
    # resampling never triggers in practice — the mechanism from
    # ``AdapterInterface`` still applies to adapters that restrict this list.
    native_timeframes: tuple[Timeframe, ...] = tuple(Timeframe)

    def __init__(
        self,
        terminal_path: str = "",
        storage: ParquetBackend | None = None,
        config: Config | None = None,
    ) -> None:
        self._config = config or Config()
        self._terminal = MT5Terminal(terminal_path, config=self._config)
        if storage is not None:
            self._storage = storage
        elif self._config.cache_enabled:
            self._storage = ParquetBackend(base_dir=str(self._config.cache_dir))
        else:
            self._storage = ParquetBackend(base_dir="")

    def connect(self) -> None:
        """Initialize the MT5 terminal connection."""
        self._terminal.initialize()

    def disconnect(self) -> None:
        """Shut down the MT5 terminal connection."""
        self._terminal.shutdown()

    # -- instruments --

    def instrument(self, symbol: str, market_type: str = "") -> Instrument:
        """Classify ``symbol`` as spot, futures, forex, CFD, etc.

        MT5 symbols are self-describing: ``MT5Terminal.symbol_info`` reads the
        broker's symbol metadata (``trade_calc_mode``, Market Watch ``path``,
        contract sizes, expiry). ``market_type`` is an optional hint
        (``"spot"``/``"futures"``) that is validated against the detected
        classification — a mismatch raises ``ProviderError``.
        """
        info = self._terminal.symbol_info(symbol)
        if info is None:
            raise ProviderError(f"Unknown MT5 symbol: {symbol!r}")
        return map_instrument(
            symbol,
            info,
            futures_modes=self._terminal.futures_calc_modes(),
            forex_modes=self._terminal.forex_calc_modes(),
            market_type=market_type,
        )

    # -- historical (sync) --

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        include_live: bool = False,
        persist: bool | None = None,
        output_format: str | None = None,
        force_refresh: bool = False,
    ) -> object:
        """Fetch OHLCV bars for a date range, validated and gap-checked.

        Large ranges are chunked and stitched by ``ops.pagination.paginate``
        (design doc sec 11). By default only fully **closed** bars are returned
        (sec 17): the last, still-forming bar is excluded before validation.
        ``include_live=True`` keeps the open bar in the output.

        Non-native timeframes (sec 7) are derived by fetching the largest
        smaller native timeframe and resampling up, controlled by
        ``Config.flag_resample`` for silent/flagged.

        ``persist`` writes the closed bars to the configured Parquet storage
        under a deterministic cache key (sec 17), and a cache hit short-circuits
        the provider unless ``force_refresh``. ``output_format`` overrides
        ``Config.output_format`` (pandas/polars/arrow/numpy).
        """
        try:
            tf_enum = Timeframe(timeframe)
            tf: int = MT5_MAP[tf_enum]
        except (KeyError, ValueError) as exc:
            raise InvalidTimeframeError(f"Unknown MT5 timeframe: {timeframe!r}") from exc

        persist = self._config.cache_enabled if persist is None else persist
        key = build_cache_key("mt5", symbol, timeframe, (start.isoformat(), end.isoformat()))

        if persist and not force_refresh and not include_live and tf_enum in self.native_timeframes:
            cached = self._try_cache_read(key)
            if cached is not None:
                logger.info("Cache hit for %s (%d rows)", key, len(cached))
                return to_output_format(cached, output_format or self._config.output_format)

        if tf_enum in self.native_timeframes:
            df = self._fetch_ohlcv_native(symbol, tf, tf_enum, start, end, include_live)
        else:
            source_tf = pick_source_timeframe(tf_enum, self.native_timeframes)
            self._log_resample(timeframe, source_tf.value)
            source = self._fetch_ohlcv_native(
                symbol, MT5_MAP[source_tf], source_tf, start, end, include_live=False
            )
            df = resample(source, tf_enum)
            validate_ohlcv(df)

        if persist:
            closed = df if not include_live else drop_incomplete_bars(df, timeframe)
            self._storage.write(key, closed)
            logger.info("Persisted OHLCV to cache key %s", key)
        return to_output_format(df, output_format or self._config.output_format)

    def _fetch_ohlcv_native(
        self,
        symbol: str,
        tf: int,
        tf_enum: Timeframe,
        start: datetime,
        end: datetime,
        include_live: bool,
    ) -> pd.DataFrame:
        """Fetch and validate ``symbol`` over the range for one native timeframe."""

        def _fetch_chunk(
            chunk_symbol: str, chunk_start: datetime, chunk_end: datetime
        ) -> pd.DataFrame:
            raw = self._terminal.copy_rates_range(chunk_symbol, tf, chunk_start, chunk_end)
            offset = self._terminal.server_offset_seconds(chunk_symbol)
            return map_ohlcv(raw, offset_seconds=offset)

        df = paginate(_fetch_chunk, symbol, tf_enum, start, end)
        if df.empty:
            return map_ohlcv(None)  # canonical empty schema (no history)
        if not include_live:
            df = drop_incomplete_bars(df, tf_enum.value)
        validate_ohlcv(df)
        self._log_gaps(symbol, tf_enum.value, start, end, df)
        return df

    def _try_cache_read(self, key: str) -> pd.DataFrame | None:
        """Return cached OHLCV for *key* if it exists, else None."""
        try:
            if self._storage.exists(key):
                df = pd.DataFrame(self._storage.read(key))
                if not df.empty and "timestamp" in df.columns:
                    return df
        except (KeyError, TypeError):
            pass
        return None

    def _log_resample(self, requested: str, source: str) -> None:
        """Warn (or log quietly) that ``requested`` is derived by resampling."""
        if self._config.flag_resample:
            logger.warning(
                "MT5 has no native %s bars; fetching %s and resampling",
                requested,
                source,
            )
        else:
            logger.info("Deriving %s from %s by resampling", requested, source)

    def _log_gaps(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, df: pd.DataFrame
    ) -> None:
        """Warn when the fetched frame has missing candles (design doc sec 18)."""
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

    def fetch_fundamentals(  # type: ignore[override]  # typed subset of base
        self,
        symbol: str,
        *,
        market_type: str = "",
    ) -> Fundamentals:
        """Fetch canonical fundamentals/reference data for ``symbol``.

        Combines ``symbol_info`` (currencies, description, classification) with
        the latest ``Tick`` (live price). ``market_type`` is forwarded to
        ``map_instrument`` so a spot/futures hint is validated, exactly as in
        ``instrument()``.
        """
        info = self._terminal.symbol_info(symbol)
        if info is None:
            raise ProviderError(f"Unknown MT5 symbol: {symbol!r}")
        tick = self._terminal.symbol_info_tick(symbol)
        fundamentals = map_fundamentals(
            symbol,
            info,
            tick=tick,
            futures_modes=self._terminal.futures_calc_modes(),
            forex_modes=self._terminal.forex_calc_modes(),
        )
        logger.info(
            "Fetched MT5 fundamentals for %s (latest=%s)",
            symbol,
            fundamentals.latest_price,
        )
        return fundamentals
