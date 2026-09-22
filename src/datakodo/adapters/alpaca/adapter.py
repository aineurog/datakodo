"""Alpaca adapter — implements the AdapterInterface for Alpaca Markets."""

from collections.abc import Sequence
from typing import Any

from datakodo.adapters.alpaca.config import AlpacaConfig
from datakodo.adapters.alpaca.rest import AlpacaREST
from datakodo.adapters.alpaca.ws import AlpacaWS
from datakodo.core.config import Config
from datakodo.core.exceptions import NotSupportedError
from datakodo.core.instruments import Instrument
from datakodo.core.interfaces import AdapterInterface


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
        **kwargs: Any,
    ) -> Any:
        """Fetch OHLCV candles (implemented in step 4)."""
        raise NotSupportedError("Alpaca fetch_ohlcv is not yet implemented.")

    # -- streaming (async) --

    async def stream_trades(self, symbol: str | Instrument) -> Any:
        """Stream live trade ticks (implemented in step 9)."""
        raise NotSupportedError("Alpaca stream_trades is not yet implemented.")
        yield  # pragma: no cover - makes this an async generator

    async def stream_orderbook(self, symbol: str | Instrument) -> Any:
        """Stream live order book snapshots (steps 8–9)."""
        raise NotSupportedError("Alpaca stream_orderbook is not yet implemented.")
        yield  # pragma: no cover - makes this an async generator
