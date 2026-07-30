"""Timeframe mapping tests."""

from datakodo.core.enums import Timeframe
from datakodo.core.timeframe import BINANCE_MAP, IBKR_MAP, MT5_MAP


class TestTimeframeMappings:
    def test_all_timeframes_in_binance_map(self):
        for tf in Timeframe:
            assert tf in BINANCE_MAP, f"Missing Binance mapping for {tf}"

    def test_all_timeframes_in_mt5_map(self):
        for tf in Timeframe:
            assert tf in MT5_MAP, f"Missing MT5 mapping for {tf}"

    def test_all_timeframes_in_ibkr_map(self):
        for tf in Timeframe:
            assert tf in IBKR_MAP, f"Missing IBKR mapping for {tf}"

    def test_binance_maps_minute_correctly(self):
        assert BINANCE_MAP[Timeframe.M1] == "1m"
        assert BINANCE_MAP[Timeframe.M5] == "5m"
        assert BINANCE_MAP[Timeframe.H1] == "1h"
        assert BINANCE_MAP[Timeframe.D1] == "1d"
        assert BINANCE_MAP[Timeframe.MN1] == "1M"

    def test_mt5_maps_to_integers(self):
        assert MT5_MAP[Timeframe.M1] == 1
        assert MT5_MAP[Timeframe.H1] == 16385
        assert MT5_MAP[Timeframe.D1] == 16408
        assert MT5_MAP[Timeframe.W1] == 32769

    def test_ibkr_maps_to_duration_strings(self):
        assert IBKR_MAP[Timeframe.M1] == "1 min"
        assert IBKR_MAP[Timeframe.M5] == "5 mins"
        assert IBKR_MAP[Timeframe.D1] == "1 day"
        assert IBKR_MAP[Timeframe.W1] == "1 week"
