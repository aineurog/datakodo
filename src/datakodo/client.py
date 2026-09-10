"""Main user-facing Client class (design doc sec 2, 24).

A thin, provider-agnostic front door. The Client only knows the provider
name and forwards to the adapter instance, so core stays independent of any
specific exchange. Adapters register themselves via Python entry points
under the ``datakodo.adapters`` group (with the built-in adapters available
directly); adding a provider never touches core.
"""

import logging
from datetime import UTC, datetime, timedelta
from importlib import import_module
from importlib.metadata import entry_points

logger = logging.getLogger(__name__)

# Built-in adapters always available even when the package is not installed
# (e.g. running from a source checkout). Entry points may register more.
_BUILTINS: dict[str, str] = {
    "binance": "datakodo.adapters.binance:BinanceAdapter",
    "mt5": "datakodo.adapters.mt5:MT5Adapter",
    "massive": "datakodo.adapters.massive:MassiveAdapter",
}

_ENTRY_POINT_GROUP = "datakodo.adapters"


def _iter_entry_points():
    """Yield entry points from the ``datakodo.adapters`` group, robustly."""
    try:
        eps = entry_points()
    except TypeError:  # pragma: no cover - very old importlib.metadata
        return []
    if hasattr(eps, "select"):
        return eps.select(group=_ENTRY_POINT_GROUP)
    return eps.get(_ENTRY_POINT_GROUP, [])


def _load_registry() -> dict[str, type]:
    """Build the adapter registry: built-ins plus any installed entry points.

    A provider whose optional dependency is not installed is skipped rather
    than breaking the whole registry, so a missing extra never prevents the
    other providers from loading.
    """
    registry: dict[str, type] = {}
    for name, spec in _BUILTINS.items():
        try:
            module_name, _, attr = spec.partition(":")
            registry[name] = getattr(import_module(module_name), attr)
        except Exception as exc:  # noqa: BLE001 - optional dependency missing
            logger.debug("Skipping built-in adapter %r: %s", name, exc)
    for ep in _iter_entry_points():
        try:
            registry[ep.name] = ep.load()
        except Exception as exc:  # noqa: BLE001 - optional dependency missing
            logger.debug("Skipping entry-point adapter %r: %s", ep.name, exc)
    return registry


class Client:
    """Entry point for all data requests.

    Instantiate with a provider name, then call the same methods you would on
    the adapter directly. Provider credentials come from the environment
    (``BINANCE_API_KEY`` etc.) or as keyword arguments forwarded to the adapter:

        client = Client("binance")
        df = client.fetch_ohlcv("BTCUSDT", "1h", start, end)
    """

    def __init__(self, provider: str, config=None, **kwargs) -> None:
        self._provider = provider
        registry = _load_registry()
        cls = registry.get(provider)
        if cls is None:
            raise ValueError(f"Unknown provider {provider!r}. Available: {sorted(registry)}.")
        self._adapter = cls(config=config, **kwargs)

    @property
    def adapter(self):
        """The underlying provider adapter (for provider-specific calls)."""
        return self._adapter

    @staticmethod
    def available_providers() -> list[str]:
        """Return the sorted list of provider names that can be instantiated."""
        return sorted(_load_registry())

    def __repr__(self) -> str:
        return f"Client(provider={self._provider!r})"

    # --- passthrough to the adapter ---

    def instrument(self, symbol, market_type=""):
        return self._adapter.instrument(symbol, market_type=market_type)

    def fetch_ohlcv(self, symbol, timeframe, start=None, end=None, **kwargs):
        """Fetch OHLCV candles. ``start``/``end`` default to the last 30 days."""
        if start is None or end is None:
            end = end or datetime.now(UTC)
            start = start or end - timedelta(days=30)
        return self._adapter.fetch_ohlcv(symbol, timeframe, start, end, **kwargs)

    def fetch_ohlcv_batch(self, symbols, timeframe, start=None, end=None, **kwargs):
        """Fetch OHLCV for many symbols. Dates default to the last 30 days."""
        if start is None or end is None:
            end = end or datetime.now(UTC)
            start = start or end - timedelta(days=30)
        return self._adapter.fetch_ohlcv_batch(symbols, timeframe, start, end, **kwargs)

    def fetch_ticks(self, symbol, start=None, end=None, **kwargs):
        return self._adapter.fetch_ticks(symbol, start=start, end=end, **kwargs)

    def fetch_orderbook_snapshot(self, symbol, **kwargs):
        return self._adapter.fetch_orderbook_snapshot(symbol, **kwargs)

    def fetch_fundamentals(self, symbol, **kwargs):
        return self._adapter.fetch_fundamentals(symbol, **kwargs)

    def search_instruments(self, query="", **kwargs):
        return self._adapter.search_instruments(query, **kwargs)

    # --- lifecycle (design doc sec 23) ---

    def __enter__(self):
        self._adapter.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._adapter.disconnect()

    def __getattr__(self, name):
        """Forward any other method (e.g. streaming) to the adapter."""
        return getattr(self._adapter, name)


_REGISTRY: dict[str, type] = {}


def register_provider(name: str, adapter_cls: type) -> None:
    """Register an adapter class under a provider name (design doc sec 24)."""
    _REGISTRY[name] = adapter_cls
