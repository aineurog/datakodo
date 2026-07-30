"""Alpaca adapter tests."""

from datetime import UTC

import pytest

from datakodo.adapters.alpaca.adapter import AlpacaAdapter


class TestAlpacaAdapter:
    def test_adapter_capabilities(self):
        adapter = AlpacaAdapter()
        assert adapter.supports_ohlcv is True
        assert adapter.supports_ticks is True
        assert adapter.supports_streaming_orderbook is True

    def test_fetch_ohlcv_not_implemented_yet(self):
        from datetime import datetime

        adapter = AlpacaAdapter()
        now = datetime.now(UTC)
        with pytest.raises(NotImplementedError):
            adapter.fetch_ohlcv("AAPL", "1h", now, now)
