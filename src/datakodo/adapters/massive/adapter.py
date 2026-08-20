"""Massive adapter — implements the AdapterInterface for Massive.com."""

import logging
from datetime import datetime
from typing import Any

from datakodo.adapters.massive.config import MassiveConfig
from datakodo.adapters.massive.mapper import map_ohlcv, map_trades
from datakodo.adapters.massive.rest import MassiveREST
from datakodo.adapters.massive.ws import MassiveWS
from datakodo.core.config import Config
from datakodo.core.exceptions import DataNotAvailableError
from datakodo.core.instruments import Instrument
from datakodo.core.interfaces import AdapterInterface, symbol_of
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
        """Map a Massive symbol prefix to its market name (section 2.3).

        Unprefixed tickers are equities; ``X:`` crypto, ``C:`` forex, ``I:``
        indices, ``O:`` options.
        """
        upper = symbol.upper()
        for prefix, market in _MARKET_BY_PREFIX.items():
            if upper.startswith(prefix):
                return market
        return "stocks"

    # -- historical (sync) --

    def fetch_ohlcv(  # type: ignore[override]  # typed signature narrower than base
        self,
        symbol: str | Instrument,
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        columns: str | list[str] = "basic",
        include_live: bool = False,
        output_format: str | None = None,
        **kwargs,
    ) -> Any:
        """Fetch OHLCV candles for a date range (design doc sec 3, 18).

        ``columns`` selects the schema: ``"basic"`` (default) returns the
        invariant minimum; ``"all"`` adds the Massive extras (``session``,
        ``vwap``, ``trades_count``); a list requests specific extras.

        By default only fully closed bars are returned (design doc sec 18);
        set ``include_live=True`` to keep the still-forming bar, marked
        ``is_closed=False``. ``output_format`` overrides ``Config.output_format``
        (pandas by default).
        """
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

    # -- streaming (async) --

    async def stream_trades(self, symbol: str | Instrument):
        symbol = symbol_of(symbol)
        async for raw in self._ws.trade_stream(symbol):
            yield map_trades(raw)
