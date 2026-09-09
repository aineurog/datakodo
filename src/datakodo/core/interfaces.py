"""Abstract base interfaces and capability checking.

Defines the contract every provider adapter must fulfill. Capability checks
are centralized here so adapters never scatter them, and the common plumbing
(batch fetch, output conversion, lifecycle) lives here too so adapters only
write the provider-specific parts.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pandas as pd

from datakodo.core.enums import Timeframe
from datakodo.core.exceptions import NotSupportedError, PaidTierRequiredError
from datakodo.core.instruments import Instrument
from datakodo.ops.output import to_output_format


def symbol_of(symbol: str | Instrument) -> str:
    """Return the provider-facing symbol string for a ``str`` or ``Instrument``.

    Every fetch method accepts either form; this normalizes both to the plain
    string the underlying provider expects, so callers never have to think
    about it.
    """
    return symbol.symbol if isinstance(symbol, Instrument) else symbol


class AdapterInterface(ABC):
    """Abstract contract every provider adapter must implement.

    Each adapter declares what it supports via capability flags. Core calls
    :func:`check_capability` before dispatching so unsupported operations fail
    clearly rather than silently (design doc sec 2).
    """

    # --- capability flags ---
    supports_ohlcv: bool = True
    supports_ticks: bool = False
    supports_orderbook_snapshot: bool = False
    supports_streaming_orderbook: bool = False
    supports_streaming_ticks: bool = False
    supports_fundamentals: bool = False
    requires_paid_tier: bool = False

    concurrency_model: str = "thread"
    """How the adapter may be driven concurrently.

    ``"thread"`` (default) means the adapter is thread-safe and batch fetch may
    fan out over a thread pool. ``"serial"`` means the adapter is not safe to
    call from multiple threads (e.g. MetaTrader 5) and batch fetch must call it
    one symbol at a time (design doc sec 2).
    """

    native_timeframes: tuple[Timeframe, ...] = tuple(Timeframe)
    """Timeframes the provider offers natively, smallest to largest.

    A requested timeframe outside this set is derived by fetching the largest
    native timeframe smaller than it and resampling up (design doc sec 7).
    Providers that lack certain timeframes restrict this list.
    """

    # --- historical (sync) ---

    @abstractmethod
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
        """Fetch OHLCV candles for a date range. Always sync/blocking.

        ``start``/``end`` default to the last 30 days (``end`` = now UTC)
        via :func:`datakodo.core.timeframe.resolve_date_range`, so callers
        may omit both. ``columns`` selects the schema: ``"basic"`` (default)
        returns the invariant minimum columns; ``"all"`` returns everything
        the provider offers; a list requests specific optional columns
        (design doc sec 9).
        """

    def _default_output_format(self) -> str:
        """Resolve the configured output format, falling back to pandas."""
        config = getattr(self, "_config", None)
        return getattr(config, "output_format", "pandas")

    def fetch_ohlcv_batch(
        self,
        symbols: Sequence[str | Instrument],
        timeframe: str,
        start: Any = None,
        end: Any = None,
        *,
        combine: bool = False,
        output_format: str | None = None,
        max_workers: int | None = None,
        columns: str | Sequence[str] = "basic",
        **kwargs: Any,
    ) -> Any:
        """Fetch OHLCV for many symbols (design doc sec 10).

        Each symbol is fetched with the adapter's ``fetch_ohlcv``; when the
        adapter's ``concurrency_model`` is ``"thread"`` the requests fan out
        over a thread pool and funnel through the provider's rate limiter.
        ``"serial"`` adapters are called one at a time. The call is still sync
        to the caller — no asyncio needed.

        Args:
            symbols: Symbols to fetch, e.g. ``["BTCUSDT", "ETHUSDT", "SOLUSDT"]``.
            timeframe: Canonical timeframe string.
            start, end: Date range bounds.
            combine: If True, return a single frame with a ``symbol`` column
                instead of a mapping. Otherwise return a ``{symbol: frame}`` dict.
            output_format: Desired output format (pandas/polars/arrow).
                Defaults to ``Config.output_format``.
            max_workers: Thread pool size. Defaults to ``min(len(symbols), 8)``.
                Ignored for ``"serial"`` adapters.
            columns: Schema selection passed to each ``fetch_ohlcv`` call.
            **kwargs: Passed through to each ``fetch_ohlcv`` call.

        Returns:
            A ``{symbol: frame}`` mapping, or one combined frame (with a
            ``symbol`` column) when ``combine=True``. Each frame is converted
            to *output_format*.
        """
        if not symbols:
            raise ValueError("At least one symbol is required.")
        from datakodo.core.timeframe import resolve_date_range

        start, end = resolve_date_range(start, end)
        fmt = output_format or self._default_output_format()
        per_call = {"columns": columns, "output_format": "pandas", **kwargs}

        if self.concurrency_model == "serial":
            results = {
                symbol_of(s): self.fetch_ohlcv(s, timeframe, start, end, **per_call)
                for s in symbols
            }
        else:
            workers = max_workers or min(len(symbols), 8)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    symbol_of(s): pool.submit(
                        self.fetch_ohlcv, s, timeframe, start, end, **per_call
                    )
                    for s in symbols
                }
                results = {s: future.result() for s, future in futures.items()}

        if combine:
            combined = pd.concat(
                [frame.assign(symbol=s) for s, frame in results.items()],
                ignore_index=True,
            )
            return to_output_format(combined, fmt)

        return {s: to_output_format(frame, fmt) for s, frame in results.items()}

    def fetch_ticks(
        self, symbol: str | Instrument, start: Any = None, end: Any = None, **kwargs: Any
    ) -> Any:
        """Fetch historical trade ticks for a date range. Always sync.

        Adapt data to the canonical Trade schema (price, size, side,
        timestamp). Default implementation raises so an adapter that
        declares ``supports_ticks`` must override it — unsupported
        capabilities fail clearly per design doc sec 2.
        """
        raise NotSupportedError("fetch_ticks is not supported by this adapter")

    def fetch_orderbook_snapshot(self, symbol: str | Instrument, **kwargs: Any) -> Any:
        """Fetch a single order book snapshot. Always sync.

        Adaptations return canonical ``OrderBook`` data. Default raises so
        an adapter declaring ``supports_orderbook_snapshot`` must override it.
        """
        raise NotSupportedError("fetch_orderbook_snapshot is not supported by this adapter")

    def fetch_fundamentals(self, symbol: str | Instrument, **kwargs: Any) -> Any:
        """Fetch fundamentals / reference data for a symbol. Always sync.

        Adaptations return canonical ``Fundamentals`` data (design doc sec 3).
        Default raises so an adapter declaring ``supports_fundamentals`` must
        override it — reference data shapes differ wildly across asset classes,
        so the default is honest rather than faking support (sec 2).
        """
        raise NotSupportedError("fetch_fundamentals is not supported by this adapter")

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

        Filters by substring ``query`` (matched against symbol and name),
        ``asset_class``, ``instrument_type``, quote currency, and exchange.
        Returns canonical ``Instrument`` descriptors. Default raises so an
        adapter that does not offer a searchable instrument list fails clearly.
        """
        raise NotSupportedError("search_instruments is not supported by this adapter")

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

    async def stream_trades(self, symbol: str | Instrument) -> Any:
        """Async generator yielding trade ticks in real time."""
        raise NotSupportedError("stream_trades is not supported by this adapter")

    async def stream_orderbook(self, symbol: str | Instrument) -> Any:
        """Async generator yielding order book snapshots / deltas."""
        raise NotSupportedError("stream_orderbook is not supported by this adapter")


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
