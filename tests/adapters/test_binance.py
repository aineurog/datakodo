"""Binance adapter tests."""

import argparse
import json
import os
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from datakodo.adapters.binance.adapter import BinanceAdapter
from datakodo.adapters.binance.mapper import map_ohlcv, map_trades
from datakodo.adapters.binance.rest import BinanceREST

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "binance"


def _generate_klines_1h() -> list:
    """Deterministic 24-candle 1h sample, shaped like a Binance kline response."""
    random.seed(7)
    step = 3_600_000
    start = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC
    price = 42000.0
    rows = []
    for _ in range(24):
        o = price
        drift = random.uniform(-0.004, 0.004)
        c = round(o * (1 + drift), 2)
        h = round(max(o, c) * (1 + random.uniform(0, 0.002)), 2)
        low = round(min(o, c) * (1 - random.uniform(0, 0.002)), 2)
        v = round(random.uniform(10.0, 18.0), 4)
        qv = round(v * (o + c) / 2, 2)
        rows.append(
            [
                start + len(rows) * step,
                f"{o}",
                f"{h}",
                f"{low}",
                f"{c}",
                f"{v}",
                start + len(rows) * step + step - 1,
                f"{qv}",
                random.randint(50, 400),
                f"{round(v * 0.5, 4)}",
                "0",
                "0",
            ]
        )
        price = c
    return rows


def _load_or_create_fixture() -> list:
    """Load the sample JSON, generating it if missing so tests are self-contained."""
    path = FIXTURES / "klines_BTCUSDT_1h.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    klines = _generate_klines_1h()
    path.write_text(json.dumps(klines, indent=2), encoding="utf-8")
    return klines


KLINES_1H = _load_or_create_fixture()


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

    def test_map_ohlcv_sample_json_returns_canonical_columns(self):
        df = map_ohlcv(KLINES_1H)
        assert list(df.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "session",
        ]

    def test_map_ohlcv_sample_json_all_candles_mapped(self):
        df = map_ohlcv(KLINES_1H)
        assert len(df) == len(KLINES_1H)

    def test_map_ohlcv_sample_json_timestamps_are_utc_aware(self):
        df = map_ohlcv(KLINES_1H)
        assert df["timestamp"].dt.tz is not None

    def test_map_ohlcv_sample_json_timestamps_monotonic(self):
        df = map_ohlcv(KLINES_1H)
        assert df["timestamp"].is_monotonic_increasing

    def test_map_ohlcv_sample_json_values_match_fixture(self):
        df = map_ohlcv(KLINES_1H)
        first = KLINES_1H[0]
        assert df.loc[0, "timestamp"] == pd.Timestamp("2024-01-01", tz="UTC")
        assert df.loc[0, "open"] == float(first[1])
        assert df.loc[0, "high"] == float(first[2])
        assert df.loc[0, "low"] == float(first[3])
        assert df.loc[0, "close"] == float(first[4])
        assert df.loc[0, "volume"] == float(first[5])

    def test_map_ohlcv_sample_json_session_is_na(self):
        df = map_ohlcv(KLINES_1H)
        assert (df["session"] == "n/a").all()

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


class TestBinanceRest:
    def test_client_init_no_ping_and_timeout(self):
        with patch("datakodo.adapters.binance.rest.Client") as client_cls:
            BinanceREST("key", "secret", timeout=5.0)
        client_cls.assert_called_once_with(
            "key",
            "secret",
            requests_params={"timeout": 5.0},
            ping=False,
        )

    def test_default_empty_credentials(self):
        with patch("datakodo.adapters.binance.rest.Client") as client_cls:
            BinanceREST()
        client_cls.assert_called_once_with(
            "",
            "",
            requests_params={"timeout": 10.0},
            ping=False,
        )

    def test_klines_returns_raw_rows_from_client(self):
        raw = [["1704067200000", "42000.0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]]
        with patch("datakodo.adapters.binance.rest.Client") as client_cls:
            client_cls.return_value.get_klines.return_value = raw
            rest = BinanceREST()
            result = rest.klines("BTCUSDT", "1h")
        assert result == raw
        client_cls.return_value.get_klines.assert_called_once_with(
            symbol="BTCUSDT", interval="1h", limit=1000
        )

    def test_klines_start_end_converted_to_epoch_ms(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2024, 1, 2, tzinfo=UTC)
        with patch("datakodo.adapters.binance.rest.Client") as client_cls:
            rest = BinanceREST()
            rest.klines("BTCUSDT", "1h", start, end)
        client_cls.return_value.get_klines.assert_called_once_with(
            symbol="BTCUSDT",
            interval="1h",
            limit=1000,
            startTime=1704067200000,
            endTime=1704153600000,
        )

    def test_klines_naive_datetime_assumed_utc(self):
        start = datetime(2024, 1, 1)
        with patch("datakodo.adapters.binance.rest.Client") as client_cls:
            rest = BinanceREST()
            rest.klines("BTCUSDT", "1h", start=start)
        kwargs = client_cls.return_value.get_klines.call_args.kwargs
        assert kwargs["startTime"] == 1704067200000

    def test_klines_without_start_end_omits_time_params(self):
        with patch("datakodo.adapters.binance.rest.Client") as client_cls:
            rest = BinanceREST()
            rest.klines("BTCUSDT", "1h")
        kwargs = client_cls.return_value.get_klines.call_args.kwargs
        assert "startTime" not in kwargs
        assert "endTime" not in kwargs


class TestBinanceAdapter:
    def test_adapter_capabilities(self):
        adapter = BinanceAdapter()
        assert adapter.supports_ohlcv is True
        assert adapter.supports_streaming_orderbook is True


def live_klines(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    interval: str = "1h",
    limit: int = 5,
) -> list:
    """Live smoke check against the real Binance API.

    Reads DATAKODO_BINANCE_API_KEY / DATAKODO_BINANCE_API_SECRET from .env.
    klines() is a public endpoint, so it works even with empty keys.
    """
    from dotenv import load_dotenv

    load_dotenv()
    rest = BinanceREST(
        os.getenv("DATAKODO_BINANCE_API_KEY") or "",
        os.getenv("DATAKODO_BINANCE_API_SECRET") or "",
    )
    rows = rest.klines(symbol, interval, start=start, end=end, limit=limit)
    print(f"Got {len(rows)} klines for {symbol} {interval}")
    for row in rows:
        print(row[:6])
    return rows


if __name__ == "__main__":
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(
            description="Live BinanceREST smoke test. Usage: python test_binance.py SYMBOL START END"
        )
        parser.add_argument("symbol", help="e.g. BTCUSDT")
        parser.add_argument("start", help="start date, e.g. 2024-01-01")
        parser.add_argument("end", help="end date, e.g. 2024-01-02")
        parser.add_argument("--interval", default="1h", help="kline interval (default 1h)")
        parser.add_argument("--limit", type=int, default=5, help="candles to fetch (default 5)")
        args = parser.parse_args()

        live_klines(
            args.symbol,
            datetime.fromisoformat(args.start),
            datetime.fromisoformat(args.end),
            interval=args.interval,
            limit=args.limit,
        )
    else:
        sys.exit(pytest.main([__file__]))
