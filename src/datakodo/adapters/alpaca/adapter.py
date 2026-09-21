"""Alpaca adapter — implements the AdapterInterface for Alpaca Markets."""

import logging
from collections.abc import Sequence
from typing import Any

from datakodo.adapters.alpaca.config import AlpacaConfig
from datakodo.adapters.alpaca.rest import AlpacaREST
from datakodo.adapters.alpaca.ws import AlpacaWS
from datakodo.core.config import Config
from datakodo.core.exceptions import NotSupportedError
from datakodo.core.instruments import Instrument
from datakodo.core.interfaces import AdapterInterface

logger = logging.getLogger(__name__)


class AlpacaAdapter(AdapterInterface):
    """Alpaca Markets adapter — US equities and crypto market data.

    Step 1 skeleton (see ``docs/alpaca_implementation.md``): capabilities
    describe the target surface honestly and every not-yet-implemented
    method raises ``NotSupportedError`` with a pointer to the step that
    implements it — never ``NotImplementedError``, never faked data.
    Endpoint logic lands in steps 2–9; this step wires config, auth
    precedence, and packaging only.
    """

    supports_ohlcv = True
    supports_ticks = True
    supports_orderbook_snapshot = False
    supports_streaming_orderbook = False
    supports_streaming_ticks = True
    supports_fundamentals = False

    concurrency_model = "thread"
    """REST is stateless/thread-safe, so batch fetch may fan out (sec 23)."""

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
        self._rest = AlpacaREST(self._alpaca.api_key, self._alpaca.api_secret)
        self._ws = AlpacaWS(self._alpaca.api_key, self._alpaca.api_secret)

    # -- historical (sync): endpoint logic lands in steps 4–8 --

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
        raise NotSupportedError(
            "Alpaca fetch_ohlcv is not yet implemented "
            "(see step 4 of docs/alpaca_implementation.md)."
        )

    # -- streaming (async): endpoint logic lands in step 9 --

    async def stream_trades(self, symbol: str | Instrument) -> Any:
        """Stream live trade ticks (implemented in step 9)."""
        raise NotSupportedError(
            "Alpaca stream_trades is not yet implemented "
            "(see step 9 of docs/alpaca_implementation.md)."
        )
        yield  # pragma: no cover - makes this an async generator

    async def stream_orderbook(self, symbol: str | Instrument) -> Any:
        """Stream live order book snapshots (see step 8 verdict, step 9)."""
        raise NotSupportedError(
            "Alpaca stream_orderbook is not yet implemented "
            "(see steps 8–9 of docs/alpaca_implementation.md)."
        )
        yield  # pragma: no cover - makes this an async generator
