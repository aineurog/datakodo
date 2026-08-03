"""Binance conformance to the shared adapter contract.

Verifies BinanceAdapter satisfies the contract defined by
``tests/adapters/contract_tests.py`` (via ``core.interfaces.check_capability``
and the canonical OHLCV schema).
"""

from datetime import UTC, datetime

import pytest

from datakodo.adapters.binance.adapter import BinanceAdapter
from datakodo.core.exceptions import NotSupportedError
from datakodo.core.interfaces import check_capability

CANONICAL_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "session"]


def test_binance_adapter_instantiates():
    adapter = BinanceAdapter()
    assert adapter is not None


def test_binance_declares_supported_capabilities():
    adapter = BinanceAdapter()
    assert adapter.supports_ohlcv is True
    assert adapter.supports_ticks is True
    assert adapter.supports_streaming_orderbook is True


def test_binance_check_capability_supported():
    check_capability(BinanceAdapter(), "supports_ohlcv")  # must not raise


def test_binance_check_capability_rejects_unsupported():
    adapter = BinanceAdapter()
    with pytest.raises(NotSupportedError):
        check_capability(adapter, "supports_fundamentals")


def test_binance_does_not_require_paid_tier():
    assert BinanceAdapter().requires_paid_tier is False


def test_binance_fetch_ohlcv_matches_canonical_contract():
    df = BinanceAdapter().fetch_ohlcv(
        "BTCUSDT", "1h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
    )
    assert list(df.columns) == CANONICAL_COLUMNS
    assert df["timestamp"].dt.tz is not None
    assert (df["session"] == "n/a").all()
