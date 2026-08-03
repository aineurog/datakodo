"""Binance adapter tests — live, against the real Binance API.

Each function is simple and verifies one piece of the adapter pipeline
with real Binance data: rest.klines() -> map_ohlcv() -> fetch_ohlcv().

All endpoints used are public, so no API keys are needed.
Run:  python tests/adapters/test_binance.py
"""

import sys
from datetime import UTC, datetime

import pytest

from datakodo.adapters.binance.adapter import BinanceAdapter
from datakodo.adapters.binance.mapper import map_ohlcv, map_trades
from datakodo.adapters.binance.rest import BinanceREST
from datakodo.core.exceptions import RateLimitError

SYMBOL = "BTCUSDT"
START = datetime(2024, 1, 1, tzinfo=UTC)
END = datetime(2024, 1, 2, tzinfo=UTC)
INTERVAL = "1h"

CANONICAL_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "session"]


def test_klines_returns_real_klines():
    rows = BinanceREST().klines(SYMBOL, INTERVAL, START, END, limit=5)
    assert len(rows) == 5
    assert len(rows[0]) == 12  # the 12-field Binance kline shape
    assert float(rows[0][1]) > 0  # open price


def test_map_ohlcv_returns_canonical_dataframe():
    rows = BinanceREST().klines(SYMBOL, INTERVAL, START, END, limit=5)
    df = map_ohlcv(rows)
    assert list(df.columns) == CANONICAL_COLUMNS
    assert len(df) == len(rows)
    assert df["timestamp"].dt.tz is not None  # always UTC-aware
    assert (df["session"] == "n/a").all()
    assert df.loc[0, "open"] == float(rows[0][1])
    assert df.loc[0, "close"] == float(rows[0][4])


def test_map_trades():
    # Binance trade payload (the shape returned by trade streams / aggTrades)
    raw = {"T": 1704067200000, "p": "50000.0", "q": "0.1", "m": True}
    trade = map_trades(raw)
    assert trade.price == 50000.0
    assert trade.size == 0.1
    assert trade.side == "buy"


def test_fetch_ohlcv_returns_canonical_dataframe():
    df = BinanceAdapter().fetch_ohlcv(SYMBOL, INTERVAL, START, END)
    assert list(df.columns) == CANONICAL_COLUMNS
    assert len(df) > 0
    assert df["timestamp"].is_monotonic_increasing


def test_fetch_ohlcv_paginates_large_range():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 3, 1, tzinfo=UTC)  # ~59 days of 1h candles > 1000 limit
    df = BinanceAdapter().fetch_ohlcv(SYMBOL, INTERVAL, start, end)
    assert len(df) > 1000  # must have been fetched in multiple requests
    assert df["timestamp"].is_monotonic_increasing
    assert df["timestamp"].is_unique
    # stitched chunks are continuous — no gaps, no duplicates
    gaps = df["timestamp"].diff().dropna().dt.total_seconds()
    assert (gaps == 3600).all()


def test_klines_weight_by_limit():
    assert BinanceREST._klines_weight(100) == 1
    assert BinanceREST._klines_weight(200) == 2
    assert BinanceREST._klines_weight(1000) == 5


def test_klines_raises_rate_limit_when_bucket_empty():
    rest = BinanceREST(rate_limit=(10.0, 0))  # no tokens available
    with pytest.raises(RateLimitError) as exc:
        rest.klines(SYMBOL, INTERVAL, START, END)
    assert exc.value.retry_after > 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
