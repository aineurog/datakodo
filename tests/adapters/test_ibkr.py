"""IBKR adapter tests."""

from datetime import UTC

import pytest

from datakodo.adapters.ibkr.adapter import IBKRAdapter


class TestIBKRAdapter:
    def test_adapter_capabilities(self):
        adapter = IBKRAdapter()
        assert adapter.supports_ohlcv is True
        assert adapter.supports_fundamentals is True
        assert adapter.supports_streaming_orderbook is True

    def test_fetch_ohlcv_not_connected_raises(self):
        from datetime import datetime

        from datakodo.core.exceptions import ConnectionError

        adapter = IBKRAdapter()
        now = datetime.now(UTC)
        with pytest.raises(ConnectionError):
            adapter.fetch_ohlcv("AAPL", "1h", now, now)

    def test_fetch_ohlcv_applies_the_fetch_contract(self, monkeypatch):
        from datetime import datetime
        from types import SimpleNamespace

        adapter = IBKRAdapter()
        bar = {
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10.0,
        }
        bars = [
            SimpleNamespace(date="2024-01-02 00:00:00", **bar),
            SimpleNamespace(date="2024-01-05 00:00:00", **bar),
        ]
        monkeypatch.setattr(adapter._client, "req_historical_data", lambda *a, **k: bars)
        df = adapter.fetch_ohlcv(
            "AAPL", "1h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 3, tzinfo=UTC)
        )
        assert len(df) == 1
        assert "is_closed" in df.columns
        assert bool(df.loc[0, "is_closed"])
