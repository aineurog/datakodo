"""Shared contract test suite — every adapter must pass these.

New adapters parametrize the fixtures with their adapter class and
sample payloads. CI runs this suite against every registered adapter.

Design doc sec 26: given mocked raw provider responses, output must
exactly match the canonical schema.
"""

import pandas as pd
import pytest

from datakodo.core.exceptions import NotSupportedError, PaidTierRequiredError
from datakodo.core.interfaces import AdapterInterface, check_capability


def _make_adapter_with_paid_tier():
    """Return an adapter that requires a paid tier for testing."""

    class PaidAdapter(AdapterInterface):
        supports_ohlcv = True
        supports_ticks = False
        requires_paid_tier = True

        def fetch_ohlcv(self, symbol, timeframe, start, end):
            return pd.DataFrame()

    return PaidAdapter()


def _make_minimal_adapter():
    """Return a minimal adapter with no streaming capabilities."""

    class MinimalAdapter(AdapterInterface):
        supports_ohlcv = True

        def fetch_ohlcv(self, symbol, timeframe, start, end):
            return pd.DataFrame()

    return MinimalAdapter()


class TestCheckCapability:
    def test_raises_paid_tier_required(self):
        adapter = _make_adapter_with_paid_tier()
        with pytest.raises(PaidTierRequiredError):
            check_capability(adapter, "supports_ohlcv")

    def test_raises_not_supported(self):
        adapter = _make_minimal_adapter()
        with pytest.raises(NotSupportedError):
            check_capability(adapter, "supports_ticks")

    def test_passes_when_capable(self):
        adapter = _make_minimal_adapter()
        # should not raise
        check_capability(adapter, "supports_ohlcv")


class TestAdapterInterfaceContract:
    """Every adapter must follow this contract."""

    def test_adapter_must_implement_fetch_ohlcv(self):
        """Abstract method enforcement — cannot instantiate without it."""

        class BrokenAdapter(AdapterInterface):
            pass  # does not implement fetch_ohlcv

        with pytest.raises(TypeError):
            BrokenAdapter()  # type: ignore[abstract]

    def test_stream_trades_default_behavior(self):
        """Default stream_trades must raise NotSupportedError."""
        adapter = _make_minimal_adapter()

        async def _run():
            async for _ in adapter.stream_trades("BTCUSDT"):
                pass

        import asyncio

        with pytest.raises(NotSupportedError):
            asyncio.run(_run())

    def test_stream_orderbook_default_behavior(self):
        """Default stream_orderbook must raise NotSupportedError."""
        adapter = _make_minimal_adapter()

        async def _run():
            async for _ in adapter.stream_orderbook("BTCUSDT"):
                pass

        import asyncio

        with pytest.raises(NotSupportedError):
            asyncio.run(_run())
