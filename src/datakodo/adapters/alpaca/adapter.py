"""Alpaca adapter — implements the AdapterInterface for Alpaca Markets."""

import logging
from collections.abc import Sequence
from typing import Any

from datakodo.adapters.alpaca.config import AlpacaConfig
from datakodo.adapters.alpaca.mapper import map_instrument, map_ohlcv
from datakodo.adapters.alpaca.rest import AlpacaREST
from datakodo.adapters.alpaca.ws import AlpacaWS
from datakodo.core.config import Config
from datakodo.core.enums import Timeframe
from datakodo.core.exceptions import (
    DataNotAvailableError,
    InvalidTimeframeError,
    NotSupportedError,
)
from datakodo.core.instruments import Instrument
from datakodo.core.interfaces import AdapterInterface, symbol_of
from datakodo.core.schemas import available_ohlcv_extras, resolve_ohlcv_columns
from datakodo.core.timeframe import resolve_date_range
from datakodo.ops.output import to_output_format
from datakodo.ops.resample import pick_source_timeframe, resample
from datakodo.ops.validation import validate_ohlcv

logger = logging.getLogger(__name__)


def _canon_class(name: str) -> str:
    """Normalize asset-class spellings (``us_equity``/``stocks`` → ``equity``)."""
    return {"stocks": "equity", "us_equity": "equity", "us_option": "equity"}.get(name, name)


class AlpacaAdapter(AdapterInterface):
    """Alpaca Markets adapter — US equities and crypto market data."""

    supports_ohlcv = True
    supports_ticks = True
    supports_orderbook_snapshot = False
    supports_streaming_orderbook = False
    supports_streaming_ticks = True
    supports_fundamentals = False

    native_timeframes: tuple[Timeframe, ...] = tuple(Timeframe)

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        config: Config | None = None,
        alpaca_config: AlpacaConfig | None = None,
    ) -> None:
        self._config = config or Config()
        self._alpaca = alpaca_config or AlpacaConfig()
        # Explicit credentials win over the environment (design doc sec 15).
        overrides: dict[str, Any] = {}
        if api_key is not None:
            overrides["api_key"] = api_key
        if api_secret is not None:
            overrides["api_secret"] = api_secret
        if overrides:
            self._alpaca = self._alpaca.model_copy(update=overrides)
        self._rest = AlpacaREST(alpaca_config=self._alpaca, config=self._config)
        self._ws = AlpacaWS(self._alpaca.api_key, self._alpaca.api_secret)

    # -- historical (sync) --

    def fetch_ohlcv(
        self,
        symbol: str | Instrument,
        timeframe: str,
        start: Any = None,
        end: Any = None,
        *,
        columns: str | Sequence[str] = "basic",
        include_live: bool = False,
        feed: str | None = None,
        adjustment: str = "raw",
        output_format: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Fetch OHLCV candles. No dates → last 30 days; closed bars only
        unless ``include_live=True``.

        A non-native timeframe is derived by fetching the nearest smaller
        native one and resampling up, flagged via ``Config.flag_resample``.
        """
        start, end = resolve_date_range(start, end)
        symbol_str = symbol_of(symbol)
        try:
            tf = Timeframe(timeframe)
        except ValueError:
            tf = None  # unknown: native path raises InvalidTimeframeError as before

        if tf is None or tf in self.native_timeframes:
            df = self._fetch_ohlcv_native(
                symbol_str, timeframe, start, end, feed, adjustment, include_live
            )
        else:
            source_tf = pick_source_timeframe(tf, self.native_timeframes)
            self._log_resample(timeframe, source_tf.value)
            source = self._fetch_ohlcv_native(
                symbol_str, source_tf.value, start, end, feed, adjustment, False
            )
            df = resample(source, tf)
            validate_ohlcv(df)

        available = available_ohlcv_extras(df.columns)
        resolved = resolve_ohlcv_columns(columns, available)
        return to_output_format(df[resolved], output_format or self._config.output_format)

    def _fetch_ohlcv_native(
        self,
        symbol_str: str,
        timeframe: str,
        start,
        end,
        feed: str | None,
        adjustment: str,
        include_live: bool,
    ) -> Any:
        """Fetch ``timeframe`` candles the provider offers natively.

        Shared by ``fetch_ohlcv`` for the direct path and as the source when
        resampling. Returns the validated full frame (base + extras).
        """
        try:
            raw = self._rest.get_bars(
                symbol_str, timeframe, start, end, feed=feed, adjustment=adjustment
            )
        except ValueError as exc:
            raise InvalidTimeframeError(f"Alpaca does not support timeframe {timeframe!r}") from exc
        df = map_ohlcv(
            raw,
            timeframe,
            session=None if "/" in symbol_str else "regular",
            columns="all",
        )

        if not include_live:
            df = df.loc[df["is_closed"]].reset_index(drop=True)

        if df.empty:
            raise DataNotAvailableError(
                f"No closed {timeframe} bars available for {symbol_str} "
                f"in [{start.isoformat()}, {end.isoformat()}]."
            )

        validate_ohlcv(df)
        return df

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
        """Search Alpaca assets. Fresh fetch per call; filters combinable."""
        results: list[Instrument] = []
        for asset in self._rest.list_assets():
            symbol = getattr(asset, "symbol", "")
            if not symbol:
                continue
            try:
                inst = map_instrument(symbol, asset)
            except Exception:
                continue
            if not self._matches(
                inst,
                query,
                asset_class,
                instrument_type,
                quote,
                exchange,
                name=getattr(asset, "name", "") or "",
            ):
                continue
            results.append(inst)
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _matches(inst, query, asset_class, instrument_type, quote, exchange, name=""):
        """Apply every optional search filter to one instrument."""
        if query:
            lowered = query.lower()
            if lowered not in inst.symbol.lower() and lowered not in name.lower():
                return False
        if asset_class is not None:
            wanted = _canon_class(str(getattr(asset_class, "value", asset_class)).lower())
            got = _canon_class(str(getattr(inst.asset_class, "value", inst.asset_class)).lower())
            if wanted != got:
                return False
        if instrument_type is not None:
            wanted_t = str(getattr(instrument_type, "value", instrument_type)).lower()
            got_t = str(getattr(inst.instrument_type, "value", inst.instrument_type)).lower()
            if wanted_t != got_t:
                return False
        if quote is not None and inst.currency.lower() != quote.lower():
            return False
        if exchange is not None and inst.exchange.lower() != exchange.lower():
            return False
        return True

    # -- streaming (async) --

    async def stream_trades(self, symbol: str | Instrument) -> Any:
        """Stream live trade ticks (implemented in step 9)."""
        raise NotSupportedError("Alpaca stream_trades is not yet implemented.")
        yield  # pragma: no cover - makes this an async generator

    async def stream_orderbook(self, symbol: str | Instrument) -> Any:
        """Stream live order book snapshots (steps 8–9)."""
        raise NotSupportedError("Alpaca stream_orderbook is not yet implemented.")
        yield  # pragma: no cover - makes this an async generator
