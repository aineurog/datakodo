"""Alpaca adapter — implements the AdapterInterface for Alpaca Markets."""

import logging
from collections.abc import Sequence
from typing import Any

from datakodo.adapters.alpaca.config import AlpacaConfig
from datakodo.adapters.alpaca.mapper import (
    map_instrument,
    map_ohlcv,
    map_rest_trades,
    price_adjustment,
)
from datakodo.adapters.alpaca.rest import AlpacaREST
from datakodo.adapters.alpaca.ws import AlpacaWS
from datakodo.core.calendar import (
    XNYS_REGULAR,
    AlwaysOpenCalendar,
    SessionProfile,
    TradingCalendar,
    resolve_calendar,
)
from datakodo.core.config import Config
from datakodo.core.enums import AssetClass, Timeframe, VolumeType
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
from datakodo.ops.validation import anchor_period_bars, apply_fetch_contract, validate_ohlcv

logger = logging.getLogger(__name__)


def _canon_class(name: str) -> str:
    """Normalize asset-class spellings (``us_equity``/``stocks`` → ``equity``)."""
    return {"stocks": "equity", "us_equity": "equity", "us_option": "equity"}.get(name, name)


def _annotate(df, *, data_tier: str, adjustment: str, volume_type: VolumeType) -> None:
    """Record the feed tier and price basis on the returned frame (sec 18)."""
    df.attrs.update(
        data_tier=data_tier,
        price_adjustment=price_adjustment(adjustment).value,
        volume_type=volume_type.value,
    )


class AlpacaAdapter(AdapterInterface):
    """Alpaca Markets adapter — US equities and crypto market data."""

    supports_ohlcv = True
    supports_ticks = True
    # No order-book depth: quotes give one best bid/ask (NBBO), while OrderBook
    # needs level lists + sequence id. A depth-1 "book" would mislead sizing.
    supports_orderbook_snapshot = False
    supports_streaming_orderbook = False
    supports_streaming_ticks = True
    # No fundamentals surface: get_asset returns the same row search already
    # maps (no currency, no as_of, no metrics), and news articles are content,
    # not a reference snapshot. Honest False beats a thin duplicate.
    supports_fundamentals = False
    supports_real_volume = True
    supports_adjusted_prices = True

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
        self._ws = AlpacaWS(self._alpaca.api_key, self._alpaca.api_secret, self._alpaca.feed)

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
            session, calendar = self._ohlcv_context(symbol_str)
            df = resample(
                source,
                tf,
                source_timeframe=source_tf,
                session=session,
                calendar=calendar,
                start=start,
                end=end,
                incomplete="drop",
            )
            validate_ohlcv(df)

        _annotate(
            df,
            data_tier=feed or self._alpaca.feed,
            adjustment=adjustment,
            volume_type=(VolumeType.BASE if "/" in symbol_str else VolumeType.SHARES),
        )
        available = available_ohlcv_extras(df.columns)
        resolved = resolve_ohlcv_columns(columns, available)
        return to_output_format(df[resolved], output_format or self._config.output_format)

    def fetch_ticks(
        self,
        symbol: str | Instrument,
        start: Any = None,
        end: Any = None,
        *,
        limit: int = 1000,
        feed: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Fetch historical trade ticks. Often paid-tier gated (honest error).

        With a ``start`` bound the whole range is paged. Without one, ``limit``
        caps the most recent trades returned.
        """
        symbol_str = symbol_of(symbol)
        rows = self._rest.list_trades(symbol_str, start, end, limit=limit, feed=feed)
        return map_rest_trades(rows)

    def _ohlcv_context(self, symbol_str: str) -> tuple[SessionProfile | None, TradingCalendar]:
        """The session profile and calendar that define this symbol's periods.

        Crypto trades 24/7 with UTC-day bars and no intraday boundary. US
        equities use the regular XNYS session; the resolved calendar supplies
        real holidays and half days when ``exchange_calendars`` is installed,
        and the built-in weekend calendar otherwise. The profile still gives
        the resampler and the fetch contract the 09:30 to 16:00 shape in both
        cases.
        """
        if "/" in symbol_str:
            return None, AlwaysOpenCalendar()
        return XNYS_REGULAR, resolve_calendar(AssetClass.EQUITY, "XNYS")

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
        session, calendar = self._ohlcv_context(symbol_str)
        df = map_ohlcv(
            raw,
            timeframe,
            session=None if "/" in symbol_str else "regular",
            columns="all",
        )
        df = anchor_period_bars(df, timeframe, calendar=calendar, session=session)
        df = apply_fetch_contract(
            df,
            timeframe,
            start,
            end,
            closed_only=not include_live,
            calendar=calendar,
            session=session,
        )

        if df.empty:
            raise DataNotAvailableError(
                f"No closed {timeframe} bars available for {symbol_str} "
                f"in [{start.isoformat()}, {end.isoformat()}]."
            )

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
                inst = map_instrument(symbol, asset, data_tier=self._alpaca.feed)
            except Exception as exc:
                logger.debug("Skipping unmappable Alpaca asset %s: %s", symbol, exc)
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
        """Yield canonical ``Trade`` records (mapped once, in ``ws``)."""
        symbol = symbol_of(symbol)
        async for trade in self._ws.trade_stream(symbol):
            yield trade

    async def stream_orderbook(self, symbol: str | Instrument) -> Any:
        """Stream live order book snapshots (unsupported: no depth feed)."""
        raise NotSupportedError("Alpaca stream_orderbook is not supported: no depth feed.")
        yield  # pragma: no cover - makes this an async generator
