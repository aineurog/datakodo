"""Massive adapter — implements the AdapterInterface for Massive.com."""

import logging
from datetime import datetime
from typing import Any

from datakodo.adapters.massive.config import MassiveConfig
from datakodo.adapters.massive.mapper import map_instrument, map_ohlcv, map_trades
from datakodo.adapters.massive.rest import MassiveREST
from datakodo.adapters.massive.ws import MassiveWS
from datakodo.core.config import Config
from datakodo.core.enums import AssetClass, InstrumentType
from datakodo.core.exceptions import DataNotAvailableError, SymbolNotFoundError
from datakodo.core.instruments import Instrument
from datakodo.core.interfaces import AdapterInterface, symbol_of
from datakodo.core.schemas import Fundamentals
from datakodo.ops.output import to_output_format
from datakodo.ops.validation import validate_ohlcv

logger = logging.getLogger(__name__)

# Massive symbol prefix -> market name (section 2.3 of the implementation doc).
_MARKET_BY_PREFIX = {
    "X:": "crypto",
    "C:": "forex",
    "I:": "index",
    "O:": "options",
}


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

    def __init__(
        self,
        api_key: str | None = None,
        config: Config | None = None,
        massive_config: MassiveConfig | None = None,
    ) -> None:
        self._config = config or Config()
        self._massive = massive_config or MassiveConfig()
        # Explicit credentials win over the environment (design doc sec 15).
        if api_key is not None:
            self._massive = self._massive.model_copy(update={"api_key": api_key})
        self._rest = MassiveREST(massive_config=self._massive, config=self._config)
        self._ws = MassiveWS(api_key or self._massive.api_key)

    def _market_of(self, symbol: str) -> str:
        """Map a Massive symbol prefix to its market name (section 2.3 of the implementation doc).

        Unprefixed tickers are equities; ``X:`` crypto, ``C:`` forex, ``I:``
        indices, ``O:`` options.
        """
        upper = symbol.upper()
        for prefix, market in _MARKET_BY_PREFIX.items():
            if upper.startswith(prefix):
                return market
        return "stocks"

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
        """Search the provider's instrument universe (design doc sec 14).

        ``query`` is a case-insensitive substring match on the symbol **and** name.
        All filters are optional and combinable. Returns canonical ``Instrument``
        descriptors. No ticker DataFrame caching is used — the ticker list is
        fetched fresh on each call (design doc sec 5, per project requirement).

        The ``instrument_type`` filter accepts ``InstrumentType`` enum values.
        The ``asset_class`` filter accepts ``AssetClass`` enum values.
        The ``quote`` filter matches the quote/currency leg of the symbol.
        The ``exchange`` filter matches the provider exchange code.
        """
        tickers = self._rest.list_tickers(
            market=asset_class,
            type=instrument_type,
            search=query,
            limit=limit,
        )
        results: list[Instrument] = []
        for ticker in tickers:
            symbol = ticker.get("ticker", "")
            if not symbol:
                continue
            try:
                inst = map_instrument(
                    symbol,
                    ticker,
                    market=self._market_of(symbol),
                )
            except Exception:
                continue
            if not self._search_match(inst, query, asset_class, instrument_type, quote, exchange):
                continue
            results.append(inst)
            if len(results) >= limit:
                break
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
        """Apply one symbol against every optional search filter (design doc sec 5)."""
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

    def fetch_fundamentals(self, symbol: str) -> Fundamentals:
        """Fetch canonical fundamentals / reference data for ``symbol``.

        Combines ``ticker_details`` (currencies, description, classification)
        with the latest tick time and the server-time offset, so ``as_of`` is
        returned in true UTC (design doc sec 3/10). An unknown symbol raises
        ``SymbolNotFoundError`` (design doc sec 16).

        This reuses the ``ticker_details()`` endpoint already implemented for
        search (Step 3), following the design doc's Step 5 order.
        """
        symbol = self._rest.ensure_symbol_known(symbol)
        info = self._rest.ticker_details(symbol=symbol)
        if not info:
            raise SymbolNotFoundError(f"Symbol {symbol!r} has no info on Massive.")

        tick = info.get("tick")

        # Map the ticker details to canonical Fundamentals
        fundamentals = Fundamentals(
            symbol=symbol,
            name=info.get("name"),
            asset_class=self._map_asset_class_from_market(info.get("market")),
            instrument_type=self._map_instrument_type_from_market(info.get("market")),
            currency=info.get("currency_name", "USD"),
            exchange=info.get("primary_exchange", "Massive"),
            as_of=self._parse_time_to_utc(tick.get("t", 0)) if tick else None,
        )
        logger.info("Fetched Massive fundamentals for %s (as_of=%s)", symbol, fundamentals.as_of)
        return fundamentals

    @staticmethod
    def _map_asset_class_from_market(market: str | None) -> AssetClass | None:
        """Map Massive market field to AssetClass enum."""
        if not market:
            return None
        market = market.lower()
        mapping = {
            "stocks": AssetClass.EQUITY,
            "crypto": AssetClass.CRYPTO,
            "forex": AssetClass.FOREX,
            "indices": AssetClass.INDEX,
            "options": AssetClass.EQUITY,
            "futures": AssetClass.EQUITY,
        }
        return mapping.get(market)

    @staticmethod
    def _map_instrument_type_from_market(market: str | None) -> InstrumentType | None:
        """Map Massive market field to InstrumentType enum."""
        if not market:
            return None
        market = market.lower()
        mapping = {
            "stocks": InstrumentType.SPOT,
            "crypto": InstrumentType.SPOT,
            "forex": InstrumentType.SPOT,
            "indices": InstrumentType.SPOT,
            "options": InstrumentType.OPTION,
            "futures": InstrumentType.FUTURE,
        }
        return mapping.get(market)

    @staticmethod
    def _parse_time_to_utc(ts: int | float | None) -> datetime | None:
        """Convert Unix ms/ns timestamp to UTC datetime."""
        if not ts:
            return None
        import datetime as _dt

        value = int(ts)
        if abs(value) > 10**15:
            value = value // 1_000_000  # ns -> ms
        return _dt.datetime.fromtimestamp(value, tz=_dt.UTC)

    # -- historical (sync) --

    def fetch_ohlcv(  # type: ignore[override]  # typed signature narrower than base
        self,
        symbol: str | Instrument,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        columns: str | list[str] = "basic",
        include_live: bool = False,
        output_format: str | None = None,
        **kwargs,
    ) -> Any:
        """Fetch OHLCV candles for a date range (design doc sec 3, 18).

        ``start``/``end`` default to the last 30 days (``end`` = now UTC);
        omit both for the latest data. ``columns`` selects the schema:
        ``"basic"`` (default) returns the invariant minimum; ``"all"`` adds
        the Massive extras (``session``, ``vwap``, ``trades_count``); a list
        requests specific extras.

        By default only fully closed bars are returned (design doc sec 18);
        set ``include_live=True`` to keep the still-forming bar, marked
        ``is_closed=False``. ``output_format`` overrides ``Config.output_format``
        (pandas by default).
        """
        from datakodo.core.timeframe import resolve_date_range

        start, end = resolve_date_range(start, end)
        symbol = symbol_of(symbol)
        market = self._market_of(symbol)
        raw = self._rest.aggs(symbol, timeframe, start, end)
        df = map_ohlcv(raw, timeframe, market=market, columns=columns)

        if not include_live:
            df = df.loc[df["is_closed"]].reset_index(drop=True)

        if df.empty:
            raise DataNotAvailableError(
                f"No closed {timeframe} bars available for {symbol} "
                f"in [{start.isoformat()}, {end.isoformat()}]."
            )

        validate_ohlcv(df)
        logger.info("Fetched %d %s OHLCV rows for %s", len(df), timeframe, symbol)
        return to_output_format(df, output_format or self._config.output_format)

    def fetch_ticks(  # type: ignore[override]  # typed signature narrower than base
        self,
        symbol: str | Instrument,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        limit: int = 1000,
        **kwargs,
    ) -> Any:
        """Fetch historical trade ticks mapped to canonical ``Trade`` records.

        Uses the REST ``list_trades`` endpoint with ``timestamp.gte/lte``
        bounds (design doc sec 7: heavy volume, chunked pagination, opt-in).
        Without ``start`` the most recent trades are fetched in a single
        call. Tick history is often paid-tier gated — that surfaces as
        ``PaidTierRequiredError``, never faked data (design doc sec 2/22).
        """
        from datakodo.adapters.massive.mapper import map_rest_trades

        symbol = symbol_of(symbol)
        raw = self._rest.list_trades(symbol, start, end, limit=limit)
        trades = map_rest_trades(raw)
        logger.info("Fetched %d trades for %s", len(trades), symbol)
        return trades

    # -- streaming (async) --

    async def stream_trades(self, symbol: str | Instrument):
        symbol = symbol_of(symbol)
        async for raw in self._ws.trade_stream(symbol):
            yield map_trades(raw)
