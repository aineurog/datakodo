"""Abstract base interfaces and capability checking.

Defines the contracts every adapter and storage backend must fulfill.
Capability checks are centralized here so adapters never scatter them.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pandas as pd

from datakodo.core.enums import Timeframe
from datakodo.core.exceptions import NotSupportedError, PaidTierRequiredError
from datakodo.ops.output import to_output_format


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

    native_timeframes: tuple[Timeframe, ...] = tuple(Timeframe)
    """Timeframes the provider offers natively, smallest to largest.

    A requested timeframe outside this set is derived by fetching the largest
    native timeframe smaller than it and resampling up (design doc sec 7).
    Providers that lack certain timeframes restrict this list.
    """

    # --- historical (sync) ---

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, start: Any, end: Any) -> Any:
        """Fetch OHLCV candles for a date range. Always sync/blocking."""

    def _default_output_format(self) -> str:
        """Resolve the configured output format, falling back to pandas."""
        config = getattr(self, "_config", None)
        return getattr(config, "output_format", "pandas")

    def fetch_ohlcv_batch(
        self,
        symbols: Sequence[str],
        timeframe: str,
        start: Any,
        end: Any,
        *,
        combine: bool = False,
        output_format: str | None = None,
        max_workers: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Fetch OHLCV for many symbols, running concurrently (design doc sec 10).

        Each symbol is fetched with the adapter's ``fetch_ohlcv`` on a worker
        of a thread pool, so requests funnel through the provider's rate limiter
        automatically. The call is still sync to the caller — no asyncio needed.

        Args:
            symbols: Symbols to fetch, e.g. ``["BTCUSDT", "ETHUSDT", "SOLUSDT"]``.
            timeframe: Canonical timeframe string.
            start, end: Date range bounds.
            combine: If True, return a single DataFrame with a ``symbol`` column
                instead of a mapping. Otherwise return a ``{symbol: frame}`` dict.
            output_format: Desired output format (pandas/polars/arrow/numpy).
                Defaults to ``Config.output_format``.
            max_workers: Thread pool size. Defaults to ``min(len(symbols), 8)``.
            **kwargs: Passed through to each ``fetch_ohlcv`` call.

        Returns:
            A ``{symbol: frame}`` mapping, or one combined DataFrame (with a
            ``symbol`` column) when ``combine=True``. Each frame is converted
            to *output_format*.
        """
        if not symbols:
            raise ValueError("At least one symbol is required.")
        fmt = output_format or self._default_output_format()
        workers = max_workers or min(len(symbols), 8)

        results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {
                pool.submit(self.fetch_ohlcv, symbol, timeframe, start, end, **kwargs): symbol
                for symbol in symbols
            }
            for future, symbol in future_map.items():
                results[symbol] = future.result()

        if combine:
            combined = pd.concat(
                [frame.assign(symbol=symbol) for symbol, frame in results.items()],
                ignore_index=True,
            )
            return to_output_format(combined, fmt)

        return {symbol: to_output_format(frame, fmt) for symbol, frame in results.items()}

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

    def fetch_fundamentals(self, symbol: str, **kwargs: Any) -> Any:
        """Fetch fundamentals / reference data for a symbol. Always sync.

        Adaptations return canonical ``Fundamentals`` data (design doc sec 3).
        Default raises so an adapter declaring ``supports_fundamentals`` must
        override it — reference data shapes differ wildly across asset classes,
        so the default is honest rather than faking support (sec 2).
        """
        raise NotSupportedError("fetch_fundamentals is not supported by this adapter")

    # --- lifecycle (design doc sec 23) ---

    def __enter__(self) -> "AdapterInterface":
        """Support ``with adapter:`` — optional connect step."""
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Support ``with adapter:`` — always disconnect."""
        self.disconnect()

    def connect(self) -> None:
        """Establish provider connections if any. Default no-op."""

    def disconnect(self) -> None:
        """Release provider connections if any. Default no-op."""

    def close(self) -> None:
        """Alias for ``disconnect`` for file-like usage."""
        self.disconnect()

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
