"""Main user-facing Client class (design doc sec 2, 24).

A thin, provider-agnostic front door. The Client only knows the provider
name and forwards to the adapter instance, so core stays independent of any
specific exchange. New providers register themselves in the registry; adding
one never touches core.
"""

from datakodo.adapters.binance import BinanceAdapter


class Client:
    """Entry point for all data requests.

    Instantiate with a provider name and optional config overrides, then call
    the same methods you would on the adapter directly:

        client = Client("binance", config=config)
        df = client.fetch_ohlcv("BTCUSDT", "1h", start, end)
    """

    def __init__(self, provider: str, config=None, **kwargs) -> None:
        self._provider = provider
        cls = _REGISTRY.get(provider)
        if cls is None:
            raise ValueError(
                f"Unknown provider {provider!r}. Available: {sorted(_REGISTRY)}."
            )
        self._adapter = cls(config=config, **kwargs)

    @property
    def adapter(self):
        """The underlying provider adapter (for provider-specific calls)."""
        return self._adapter

    def __repr__(self) -> str:
        return f"Client(provider={self._provider!r})"

    # --- passthrough to the adapter ---

    def instrument(self, symbol, market_type="spot"):
        return self._adapter.instrument(symbol, market_type=market_type)

    def fetch_ohlcv(self, symbol, timeframe, start, end, **kwargs):
        return self._adapter.fetch_ohlcv(symbol, timeframe, start, end, **kwargs)

    def fetch_ohlcv_batch(self, symbols, timeframe, start, end, **kwargs):
        return self._adapter.fetch_ohlcv_batch(symbols, timeframe, start, end, **kwargs)

    def fetch_ticks(self, symbol, start=None, end=None, **kwargs):
        return self._adapter.fetch_ticks(symbol, start=start, end=end, **kwargs)

    def fetch_orderbook_snapshot(self, symbol, **kwargs):
        return self._adapter.fetch_orderbook_snapshot(symbol, **kwargs)

    def fetch_fundamentals(self, symbol, **kwargs):
        return self._adapter.fetch_fundamentals(symbol, **kwargs)

    # --- lifecycle (design doc sec 23) ---

    def __enter__(self):
        self._adapter.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._adapter.disconnect()

    def __getattr__(self, name):
        """Forward any other method (e.g. streaming) to the adapter."""
        return getattr(self._adapter, name)


_REGISTRY: dict[str, type] = {
    "binance": BinanceAdapter,
}


def register_provider(name: str, adapter_cls: type) -> None:
    """Register an adapter class under a provider name (design doc sec 24)."""
    _REGISTRY[name] = adapter_cls
