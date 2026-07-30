"""MT5 adapter tests."""

from datetime import UTC

import pytest

from datakodo.adapters.mt5.adapter import MT5Adapter


class TestMT5Adapter:
    def test_adapter_capabilities(self):
        adapter = MT5Adapter()
        assert adapter.supports_ohlcv is True
        assert adapter.supports_ticks is False
        assert adapter.supports_streaming_orderbook is False

    def test_fetch_ohlcv_not_connected_raises(self):
        from datetime import datetime

        from datakodo.core.exceptions import ConnectionError

        adapter = MT5Adapter()
        now = datetime.now(UTC)
        with pytest.raises(ConnectionError):
            adapter.fetch_ohlcv("EURUSD", "1h", now, now)
