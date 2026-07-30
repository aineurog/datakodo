"""Polygon adapter tests."""

from datetime import UTC

import pytest

from datakodo.adapters.polygon.adapter import PolygonAdapter


class TestPolygonAdapter:
    def test_adapter_capabilities(self):
        adapter = PolygonAdapter()
        assert adapter.supports_ohlcv is True
        assert adapter.supports_streaming_orderbook is False
        assert adapter.supports_fundamentals is True

    def test_fetch_ohlcv_not_implemented_yet(self):
        from datetime import datetime

        adapter = PolygonAdapter()
        now = datetime.now(UTC)
        with pytest.raises(NotImplementedError):
            adapter.fetch_ohlcv("AAPL", "1h", now, now)
