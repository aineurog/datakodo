"""Alpaca adapter — implements the AdapterInterface for Alpaca Markets."""

from collections.abc import Sequence
from typing import Any

from datakodo.adapters.alpaca.config import AlpacaConfig
from datakodo.adapters.alpaca.mapper import map_ohlcv
from datakodo.adapters.alpaca.rest import AlpacaREST
from datakodo.adapters.alpaca.ws import AlpacaWS
from datakodo.core.config import Config
from datakodo.core.exceptions import (
    DataNotAvailableError,
    InvalidTimeframeError,
    NotSupportedError,
)
from datakodo.core.instruments import Instrument
from datakodo.core.interfaces import AdapterInterface, symbol_of
from datakodo.core.timeframe import resolve_date_range
from datakodo.ops.output import to_output_format
from datakodo.ops.validation import validate_ohlcv


class AlpacaAdapter(AdapterInterface):
    """Alpaca Markets adapter — US equities and crypto market data."""

    supports_ohlcv = True
    supports_ticks = True
    supports_orderbook_snapshot = False
    supports_streaming_orderbook = False
    supports_streaming_ticks = True
    supports_fundamentals = False

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
        unless ``include_live=True``."""
        start, end = resolve_date_range(start, end)
        symbol_str = symbol_of(symbol)
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
            columns=columns,
        )

        if not include_live:
            df = df.loc[df["is_closed"]].reset_index(drop=True)

        if df.empty:
            raise DataNotAvailableError(
                f"No closed {timeframe} bars available for {symbol_str} "
                f"in [{start.isoformat()}, {end.isoformat()}]."
            )

        validate_ohlcv(df)
        return to_output_format(df, output_format or self._config.output_format)

    # -- streaming (async) --

    async def stream_trades(self, symbol: str | Instrument) -> Any:
        """Stream live trade ticks (implemented in step 9)."""
        raise NotSupportedError("Alpaca stream_trades is not yet implemented.")
        yield  # pragma: no cover - makes this an async generator

    async def stream_orderbook(self, symbol: str | Instrument) -> Any:
        """Stream live order book snapshots (steps 8–9)."""
        raise NotSupportedError("Alpaca stream_orderbook is not yet implemented.")
        yield  # pragma: no cover - makes this an async generator
