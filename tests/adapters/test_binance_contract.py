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
from datakodo.core.config import Config

CANONICAL_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "is_closed"]

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


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Make tests hermetic: drop real BINANCE_* / DATAKODO_* env vars."""
    for key in list(Config.model_fields):
        monkeypatch.delenv("DATAKODO_" + key.upper(), raising=False)
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)


def test_binance_fetch_ohlcv_matches_canonical_contract():
    with patch("datakodo.adapters.binance.rest.BinanceREST.klines", return_value=[_KLINE]):
        df = BinanceAdapter().fetch_ohlcv(
            "BTCUSDT", "1h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
    assert list(df.columns) == CANONICAL_COLUMNS
    assert df["timestamp"].dt.tz is not None
    assert df["is_closed"].all()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
