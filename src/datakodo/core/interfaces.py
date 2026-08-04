"""Abstract base interfaces and capability checking.

Defines the contracts every adapter and storage backend must fulfill.
Capability checks are centralized here so adapters never scatter them.
"""

from abc import ABC, abstractmethod
from typing import Any

from datakodo.core.exceptions import NotSupportedError, PaidTierRequiredError


class AdapterInterface(ABC):
    """Abstract contract every provider adapter must implement.

    Each adapter declares what it supports via capability flags.
    Core calls `check_capability()` before dispatching to the adapter
    so unsupported operations fail clearly rather than silently.
    """

    # --- capability flags ---
    supports_ohlcv: bool = True
    supports_ticks: bool = False
    supports_orderbook_snapshot: bool = False
    supports_streaming_orderbook: bool = False
    supports_streaming_ticks: bool = False
    supports_fundamentals: bool = False
    requires_paid_tier: bool = False

    # --- historical (sync) ---

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, start: Any, end: Any) -> Any:
        """Fetch OHLCV candles for a date range. Always sync/blocking."""

    def fetch_ticks(self, symbol: str, start: Any = None, end: Any = None, **kwargs: Any) -> Any:
        """Fetch historical trade ticks for a date range. Always sync.

        Adapt data to the canonical Trade schema (price, size, side,
        timestamp). Default implementation raises so an adapter that
        declares ``supports_ticks`` must override it — unsupported
        capabilities fail clearly per design doc sec 2.
        """
        raise NotSupportedError("fetch_ticks is not supported by this adapter")

    def fetch_orderbook_snapshot(self, symbol: str, **kwargs: Any) -> Any:
        """Fetch a single order book snapshot. Always sync.

        Adaptations return canonical ``OrderBook`` data. Default raises so
        an adapter declaring ``supports_orderbook_snapshot`` must override it.
        """
        raise NotSupportedError("fetch_orderbook_snapshot is not supported by this adapter")

    # --- streaming (async) ---

    async def stream_trades(self, symbol: str) -> Any:
        """Async generator yielding trade ticks in real time."""
        raise NotSupportedError("stream_trades is not supported by this adapter")

    async def stream_orderbook(self, symbol: str) -> Any:
        """Async generator yielding order book snapshots / deltas."""
        raise NotSupportedError("stream_orderbook is not supported by this adapter")


class StorageBackend(ABC):
    """Abstract interface for pluggable storage backends.

    Implementations: Parquet (Phase 1), TimescaleDB (later phase).
    """

    @abstractmethod
    def write(self, key: str, data: Any) -> None:
        """Persist data under the given key."""

    @abstractmethod
    def read(self, key: str) -> Any:
        """Read data for the given key. Raises KeyError if missing."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True if data exists for the given key."""


def check_capability(adapter: AdapterInterface, capability: str) -> None:
    """Verify an adapter supports *capability* before dispatch.

    Raises:
        PaidTierRequiredError: If the endpoint requires a paid tier on
            this adapter but none is configured.
        NotSupportedError: If the adapter does not implement *capability*
            at all.
    """
    if adapter.requires_paid_tier:
        raise PaidTierRequiredError(
            f"This endpoint requires a paid {adapter.__class__.__name__} tier. "
            "See the provider's pricing page for details."
        )

    if not getattr(adapter, capability, False):
        raise NotSupportedError(f"{adapter.__class__.__name__} does not support {capability!r}.")
