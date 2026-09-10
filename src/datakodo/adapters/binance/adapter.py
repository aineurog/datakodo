"""Binance adapter: implements the AdapterInterface for Binance.

Supports the spot and USD-M perpetual futures markets. OHLCV is fetched,
validated, and (when a non-native timeframe is requested) resampled locally.
No local cache is kept: every fetch returns the provider's latest truth
(design doc sec 18).
"""

import logging
from datetime import UTC, datetime
from typing import Any

from datakodo.adapters.binance.config import BinanceConfig
from datakodo.adapters.binance.mapper import (
    map_fundamentals,
    map_ohlcv,
    map_orderbook,
    map_ticks,
    map_trades,
    quote_asset,
)
from datakodo.adapters.binance.rest import BinanceREST
from datakodo.adapters.binance.ws import BinanceWS
from datakodo.core.config import Config
from datakodo.core.enums import AssetClass, InstrumentType, Timeframe
from datakodo.core.exceptions import DataNotAvailableError
from datakodo.core.instruments import CryptoPerpetualExtension, Instrument
from datakodo.core.interfaces import AdapterInterface, symbol_of
from datakodo.core.schemas import (
    OrderBook,
    Trade,
    available_ohlcv_extras,
    resolve_ohlcv_columns,
)
from datakodo.core.timeframe import BINANCE_MAP
from datakodo.ops.output import to_output_format
from datakodo.ops.pagination import paginate
from datakodo.ops.resample import pick_source_timeframe, resample
from datakodo.ops.validation import add_is_closed, validate_ohlcv

logger = logging.getLogger(__name__)


class BinanceAdapter(AdapterInterface):
    """Binance spot/perpetual futures market data adapter.

    Capabilities: OHLCV (spot + futures), ticks (historical + streaming),
    order book snapshot + streaming, fundamentals, and instrument search.
    """

    supports_ohlcv = True
    supports_ticks = True
    supports_orderbook_snapshot = True
    supports_streaming_orderbook = True
    supports_streaming_ticks = True
    supports_fundamentals = True

    native_timeframes: tuple[Timeframe, ...] = tuple(Timeframe)
    """Binance offers every canonical timeframe natively, so no resampling is
    needed for Binance — the mechanism (design doc sec 8) still runs for
    adapters that restrict this list."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        config: Config | None = None,
        binance_config: BinanceConfig | None = None,
    ) -> None:
        self._config = config or Config()
        self._binance = binance_config or BinanceConfig()
        # Explicit credentials win over the environment (design doc sec 15).
        updates: dict[str, Any] = {}
        if api_key is not None:
            updates["api_key"] = api_key
        if api_secret is not None:
            updates["api_secret"] = api_secret
        if updates:
            self._binance = self._binance.model_copy(update=updates)
        self._rest = BinanceREST(binance_config=self._binance, config=self._config)
        self._ws = BinanceWS(binance_config=self._binance)

    # -- instruments --

    def instrument(self, symbol: str, market_type: str = "spot") -> Instrument:
        """Build a canonical Instrument descriptor for ``symbol``.

        Perpetual futures are described with a ``CryptoPerpetualExtension``;
        spot pairs use a plain base ``Instrument``.
        """
        market_type = market_type or self._binance.market_type
        currency = quote_asset(symbol)
        if market_type == "futures":
            return Instrument(
                symbol=symbol,
                provider_symbol=symbol,
                asset_class=AssetClass.CRYPTO,
                instrument_type=InstrumentType.PERPETUAL,
                exchange="Binance",
                currency=currency,
                crypto_perpetual=CryptoPerpetualExtension(contract_size=1.0),
            )
        return Instrument(
            symbol=symbol,
            provider_symbol=symbol,
            asset_class=AssetClass.CRYPTO,
            instrument_type=InstrumentType.SPOT,
            exchange="Binance",
            currency=currency,
        )

    def search_instruments(  # type: ignore[override]
        self,
        query: str = "",
        *,
        asset_class: Any = None,
        instrument_type: Any = None,
        quote: str | None = None,
        exchange: str | None = None,
        limit: int = 100,
        market_type: str = "",
    ) -> list[Instrument]:
        """Search the Binance instrument universe (design doc sec 5).

        Fetches ``exchangeInfo`` once and filters client-side. ``query`` is a
        case-insensitive substring match on the symbol. All filters are
        optional and combinable.
        """
        market_type = market_type or self._binance.market_type
        entries = self._rest.all_symbols(market_type)
        results: list[Instrument] = []
        for entry in entries:
            inst = self._instrument_from_entry(entry, market_type)
            if query and query.lower() not in inst.symbol.lower():
                continue
            if asset_class is not None and inst.asset_class != asset_class:
                continue
            if instrument_type is not None and inst.instrument_type != instrument_type:
                continue
            if quote is not None and (entry.get("quoteAsset") or "").lower() != quote.lower():
                continue
            if exchange is not None and inst.exchange.lower() != exchange.lower():
                continue
            results.append(inst)
            if len(results) >= limit:
                break
        logger.info("Binance %s search returned %d instruments", market_type, len(results))
        return results

    def _instrument_from_entry(self, entry: dict, market_type: str) -> Instrument:
        """Build an Instrument from a raw ``exchangeInfo`` symbol entry."""
        symbol = entry.get("symbol", "")
        inst_type = InstrumentType.PERPETUAL if market_type == "futures" else InstrumentType.SPOT
        return Instrument(
            symbol=symbol,
            provider_symbol=symbol,
            asset_class=AssetClass.CRYPTO,
            instrument_type=inst_type,
            exchange="Binance",
            currency=entry.get("quoteAsset") or quote_asset(symbol),
        )

    # -- historical (sync) --

    def fetch_ohlcv(  # type: ignore[override]
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        columns: str | list[str] = "basic",
        market_type: str = "",
        include_live: bool = False,
        output_format: str | None = None,
    ) -> Any:
        """Fetch OHLCV candles for a date range (design doc sec 6, 8).

        ``market_type`` selects spot or USD-M futures klines; it defaults to the
        value configured on the adapter. By default only fully **closed** bars
        are returned (design doc sec 18); set ``include_live=True`` to keep the
        still-forming bar, marked ``is_closed=False``.

        If ``timeframe`` is not natively available it is derived by fetching the
        nearest smaller native timeframe and resampling up (design doc sec 8),
        controlled by ``Config.flag_resample`` for silent/flagged. Resampled
        output is always fully closed.

        ``columns`` selects the schema (design doc sec 3): ``"basic"`` returns
        the invariant minimum; ``"all"`` returns every extra column Binance
        offers; a list requests specific optional columns.

        ``output_format`` selects the user-facing representation (design doc
        sec 13): pandas (default), polars, or arrow — a per-call override of
        ``Config.output_format``.

        Omit ``start``/``end`` for the last 30 days (``end`` = now UTC).
        """
        from datakodo.core.timeframe import resolve_date_range

        start, end = resolve_date_range(start, end)
        symbol = symbol_of(symbol)
        market_type = market_type or self._binance.market_type
        tf = Timeframe(timeframe)

        if tf in self.native_timeframes:
            df = self._fetch_ohlcv_native(symbol, timeframe, start, end, market_type, include_live)
        else:
            source_tf = pick_source_timeframe(tf, self.native_timeframes)
            self._log_resample(timeframe, source_tf.value)
            source = self._fetch_ohlcv_native(
                symbol, source_tf.value, start, end, market_type, include_live=False
            )
            df = resample(source, tf)
            validate_ohlcv(df)
            logger.info(
                "Resampled %s -> %s (%d bars) for %s %s",
                source_tf.value,
                timeframe,
                len(df),
                market_type,
                symbol,
            )

        available = available_ohlcv_extras(df.columns)
        resolved = resolve_ohlcv_columns(columns, available)
        return to_output_format(df[resolved], output_format or self._config.output_format)

    def _fetch_ohlcv_native(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        market_type: str,
        include_live: bool,
    ) -> Any:
        """Fetch ``timeframe`` candles that the provider offers natively.

        Shared by ``fetch_ohlcv`` for the direct path and as the source when
        resampling. Returns validated bars with an ``is_closed`` column;
        ``include_live`` keeps the still-forming bar (marked ``is_closed=False``),
        otherwise only closed bars are returned.
        """
        tf = Timeframe(timeframe)
        interval = BINANCE_MAP[tf]

        def _fetch_chunk(chunk_symbol: str, chunk_start: datetime, chunk_end: datetime) -> Any:
            raw = self._rest.klines(
                chunk_symbol, interval, chunk_start, chunk_end, market_type=market_type
            )
            return map_ohlcv(raw)

        df = paginate(_fetch_chunk, symbol, tf, start, end)
        df = add_is_closed(df, timeframe)

        if not include_live:
            df = df.loc[df["is_closed"]].reset_index(drop=True)

        if df.empty:
            raise DataNotAvailableError(
                f"No closed {timeframe} bars available for {symbol} ({market_type}) "
                f"in [{start.isoformat()}, {end.isoformat()}]."
            )

        validate_ohlcv(df)
        logger.info("Fetched %d %s OHLCV rows for %s %s", len(df), timeframe, market_type, symbol)
        return df

    def _log_resample(self, requested: str, source: str) -> None:
        """Warn (or log quietly) that ``requested`` is derived by resampling."""
        if self._config.flag_resample:
            logger.warning(
                "%s has no native %s klines; fetching %s and resampling",
                self.__class__.__name__,
                requested,
                source,
            )
        else:
            logger.info("Deriving %s from %s by resampling", requested, source)

    def fetch_ticks(  # type: ignore[override]  # typed signature narrower than base
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        limit: int = 1000,
        market_type: str = "",
    ) -> list[Trade]:
        """Fetch historical trade ticks (aggTrade) mapped to canonical Trades.

        ``market_type`` defaults to the configured market. When a ``start`` is
        given the whole range is paged automatically (one-hour windows, deduped
        by aggregate id); without a ``start`` the most recent trades are fetched
        in a single call. Returns a list of canonical ``Trade`` records.
        """
        symbol = symbol_of(symbol)
        market_type = market_type or self._binance.market_type
        if start is not None:
            raw = self._rest.ticks_all(
                symbol,
                start,
                end if end is not None else datetime.now(UTC),
                limit=limit,
                market_type=market_type,
            )
        else:
            raw = self._rest.ticks(symbol, start, end, limit=limit, market_type=market_type)
        trades = map_ticks(raw)
        logger.info("Fetched %d %s trades for %s", len(trades), market_type, symbol)
        return trades

    def fetch_orderbook_snapshot(  # type: ignore[override]  # typed subset of base
        self,
        symbol: str,
        *,
        limit: int = 20,
        market_type: str = "",
    ) -> OrderBook:
        """Fetch a single canonical order book snapshot for ``symbol``."""
        symbol = symbol_of(symbol)
        market_type = market_type or self._binance.market_type
        raw = self._rest.orderbook(symbol, limit=limit, market_type=market_type)
        book = map_orderbook(raw)
        logger.info(
            "Fetched %s order book for %s (bids=%d asks=%d)",
            market_type,
            symbol,
            len(book.bids),
            len(book.asks),
        )
        return book

    def fetch_fundamentals(  # type: ignore[override]  # typed subset of base
        self,
        symbol: str,
        *,
        market_type: str = "",
    ) -> Any:
        """Fetch canonical fundamentals for ``symbol`` (design doc sec 3).

        Combines the Binance 24h rolling ticker (live price/volume stats) with
        exchange info (base/quote asset, trading status, permissions). Returns a
        canonical ``Fundamentals`` record with a ``CryptoFundamentals`` block.
        """
        symbol = symbol_of(symbol)
        market_type = market_type or self._binance.market_type
        ticker = self._rest.ticker_24h(symbol, market_type=market_type)
        info = self._rest.exchange_info(symbol, market_type=market_type)
        fundamentals = map_fundamentals(ticker, info)
        latest = fundamentals.crypto.latest_price if fundamentals.crypto else None
        logger.info("Fetched %s fundamentals for %s (latest=%s)", market_type, symbol, latest)
        return fundamentals

    # -- streaming (async) --

    async def stream_trades(  # type: ignore[override]
        self, symbol: str, market_type: str = "", max_messages: int | None = None
    ):
        market_type = market_type or self._binance.market_type
        async for raw in self._ws.trade_stream(
            symbol, market_type=market_type, max_messages=max_messages
        ):
            yield map_trades(raw)

    async def stream_orderbook(  # type: ignore[override]
        self, symbol: str, market_type: str = "", max_messages: int | None = None
    ):
        market_type = market_type or self._binance.market_type
        async for raw in self._ws.orderbook_stream(
            symbol, market_type=market_type, max_messages=max_messages
        ):
            yield raw
