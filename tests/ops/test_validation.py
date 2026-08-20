"""Data quality validation tests."""

from datetime import date, time

import pandas as pd
import pytest

from datakodo.core.calendar import ExchangeCalendar, SessionWindow, WeekendClosedCalendar
from datakodo.core.enums import Session
from datakodo.core.exceptions import DataValidationError
from datakodo.ops.validation import detect_gaps, validate_ohlcv


class TestValidateOHLCV:
    def test_valid_dataframe_passes(self, sample_ohlcv_df):
        validate_ohlcv(sample_ohlcv_df)

    def test_empty_dataframe_raises(self):
        df = pd.DataFrame()
        with pytest.raises(DataValidationError, match="empty"):
            validate_ohlcv(df)

    def test_missing_columns_raises(self):
        df = pd.DataFrame({"wrong_col": [1, 2, 3]})
        with pytest.raises(DataValidationError, match="Missing"):
            validate_ohlcv(df)

    def test_negative_prices_raise(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.loc[2, "open"] = -1.0
        with pytest.raises(DataValidationError, match="negative"):
            validate_ohlcv(df)

    def test_negative_volume_raises(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.loc[1, "volume"] = -100.0
        with pytest.raises(DataValidationError, match="negative"):
            validate_ohlcv(df)

    def test_high_below_low_raises(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.loc[3, "high"] = 50.0
        df.loc[3, "low"] = 100.0
        with pytest.raises(DataValidationError, match="high < low"):
            validate_ohlcv(df)

    def test_non_monotonic_timestamps_raise(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        timestamps = df["timestamp"].tolist()
        timestamps[2], timestamps[3] = timestamps[3], timestamps[2]
        df["timestamp"] = timestamps
        with pytest.raises(DataValidationError, match="monotonically"):
            validate_ohlcv(df)

    def test_duplicate_timestamps_raise(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        # Make timestamps monotonically increasing, then create a duplicate.
        df = df.sort_values("timestamp")
        df.loc[4, "timestamp"] = df.loc[0, "timestamp"]
        df = df.sort_values("timestamp")
        with pytest.raises(DataValidationError, match="Duplicate"):
            validate_ohlcv(df)


def _ohlcv(timestamps) -> pd.DataFrame:
    """A minimal OHLCV frame with the given tz-aware timestamps."""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(timestamps, utc=True),
            "open": [1.0] * len(timestamps),
            "high": [1.1] * len(timestamps),
            "low": [0.9] * len(timestamps),
            "close": [1.05] * len(timestamps),
            "volume": [100.0] * len(timestamps),
        }
    )


class TestDetectGapsCalendarAware:
    def test_weekend_gap_suppressed(self):
        """1h bars Friday 22:00 -> Monday 00:00 is a weekend closure."""
        df = _ohlcv(["2026-01-09 22:00", "2026-01-12 00:00"])
        assert not detect_gaps(df, "1h").empty  # flagged without a calendar
        assert detect_gaps(df, "1h", calendar=WeekendClosedCalendar()).empty

    def test_same_day_gap_always_flagged(self):
        """A hole within one day is real even with a calendar."""
        df = _ohlcv(["2026-01-12 09:00", "2026-01-12 12:00"])
        gaps = detect_gaps(df, "1h", calendar=WeekendClosedCalendar())
        assert len(gaps) == 1
        assert int(gaps["gap_missing"].iloc[0]) == 2

    def test_trading_day_gap_flagged(self):
        """Daily bars Friday -> Tuesday skip Monday, a trading day."""
        df = _ohlcv(["2026-01-09", "2026-01-13"])
        gaps = detect_gaps(df, "1d", calendar=WeekendClosedCalendar())
        assert len(gaps) == 1
        assert int(gaps["gap_missing"].iloc[0]) == 3

    def test_holiday_gap_suppressed(self):
        """Daily bars Wed Dec 31 -> Fri Jan 2 with Jan 1 a holiday."""
        regular = SessionWindow(Session.REGULAR, time(9, 30), time(16, 0))
        schedule = {weekday: (regular,) for weekday in range(5)}
        cal = ExchangeCalendar("America/New_York", schedule, holidays=[date(2026, 1, 1)])
        df = _ohlcv(["2025-12-31", "2026-01-02"])
        assert not detect_gaps(df, "1d").empty  # flagged without a calendar
        assert detect_gaps(df, "1d", calendar=cal).empty
