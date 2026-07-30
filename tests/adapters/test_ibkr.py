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
