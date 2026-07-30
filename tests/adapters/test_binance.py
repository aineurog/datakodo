"""Binance adapter tests."""

from datetime import UTC

import pytest

from datakodo.adapters.binance.adapter import BinanceAdapter
from datakodo.adapters.binance.mapper import map_ohlcv, map_trades


class TestBinanceMapper:
    def test_map_ohlcv_empty(self):
        df = map_ohlcv([])
        assert df.empty
        assert "timestamp" in df.columns

    def test_map_ohlcv_single_candle(self):
        # Binance kline: [open_time, open, high, low, close, volume, ...]
        raw = [
            [
                1704067200000,  # 2024-01-01 00:00:00
                "100.0",
                "102.0",
                "99.0",
                "101.0",
                "5000.0",
                1704070799999,
                "505000.0",
                100,
                "2500.0",
                "252500.0",
                "0",
            ]
        ]
        df = map_ohlcv(raw)
        assert len(df) == 1
        assert df.loc[0, "open"] == 100.0
        assert df.loc[0, "close"] == 101.0
        assert df.loc[0, "session"] == "n/a"

    def test_map_trades(self):
        raw = {"T": 1704067200000, "p": "50000.0", "q": "0.1", "m": True}
        trade = map_trades(raw)
        assert trade.price == 50000.0
        assert trade.size == 0.1
        assert trade.side == "buy"

    def test_map_trades_sell_side(self):
        raw = {"T": 1704067200000, "p": "50000.0", "q": "0.1", "m": False}
        trade = map_trades(raw)
        assert trade.side == "sell"


class TestBinanceAdapter:
    def test_adapter_capabilities(self):
        adapter = BinanceAdapter()
        assert adapter.supports_ohlcv is True
        assert adapter.supports_streaming_orderbook is True

    def test_fetch_ohlcv_not_implemented_yet(self):
        from datetime import datetime

        adapter = BinanceAdapter()
        now = datetime.now(UTC)
        with pytest.raises(NotImplementedError):
            adapter.fetch_ohlcv("BTCUSDT", "1h", now, now)
