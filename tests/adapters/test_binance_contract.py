"""Binance conformance to the shared adapter contract.

Verifies BinanceAdapter satisfies the contract defined by
``tests/adapters/contract_tests.py`` (via ``core.interfaces.check_capability``
and the canonical OHLCV schema).

Live API calls are mocked so the suite passes in CI without Binance access.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from datakodo.adapters.binance.adapter import BinanceAdapter
from datakodo.core.exceptions import NotSupportedError
from datakodo.core.interfaces import check_capability

CANONICAL_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "session"]

# Canonical raw kline shape: [open_time, open, high, low, close, volume,
#  close_time, quote_volume, trades, taker_buy_base, taker_buy_quote, ignore]
_KLINE = [
    1704067200000,
    "50000.0",
    "51000.0",
    "49500.0",
    "50500.0",
    "100.0",
    1704070799999,
    "5050000.0",
    1000,
    "50.0",
    "2525000.0",
    "0",
]


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
    with patch("datakodo.adapters.binance.rest.BinanceREST.klines", return_value=[_KLINE]):
        df = BinanceAdapter().fetch_ohlcv(
            "BTCUSDT", "1h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
    assert list(df.columns) == CANONICAL_COLUMNS
    assert df["timestamp"].dt.tz is not None
    assert (df["session"] == "n/a").all()
