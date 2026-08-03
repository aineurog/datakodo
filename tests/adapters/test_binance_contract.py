"""Binance conformance to the shared adapter contract.

Verifies BinanceAdapter satisfies the contract defined by
``tests/adapters/contract_tests.py`` (via ``core.interfaces.check_capability``
and the canonical OHLCV schema).

Note: capability flags, ``check_capability``, and paid-tier assertions live
in ``test_binance_components.py``; this file keeps only the contract tests
unique to conformance.

Run:
    python tests/adapters/test_binance_contract.py
"""

from datetime import UTC, datetime

import pytest

from datakodo.adapters.binance.adapter import BinanceAdapter
from datakodo.core.config import Config

CANONICAL_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "session"]


@pytest.fixture(autouse=True)
def _clear_datakodo_env(monkeypatch):
    """Make tests hermetic: drop real DATAKODO_* env vars."""
    for key in list(Config.model_fields) + [
        "DATAKODO_BINANCE_API_KEY",
        "DATAKODO_BINANCE_API_SECRET",
    ]:
        monkeypatch.delenv("DATAKODO_" + key.upper(), raising=False)


def test_binance_fetch_ohlcv_matches_canonical_contract():
    df = BinanceAdapter().fetch_ohlcv(
        "BTCUSDT", "1h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
    )
    assert list(df.columns) == CANONICAL_COLUMNS
    assert df["timestamp"].dt.tz is not None
    assert (df["session"] == "n/a").all()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
