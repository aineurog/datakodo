"""Resampling tests."""

import pandas as pd
import pytest

from datakodo.core.enums import Timeframe
from datakodo.ops.resample import resample


@pytest.fixture
def minute_df():
    """4 hours of 1-minute OHLCV data."""
    idx = pd.date_range("2024-01-01 09:00", periods=240, freq="1min", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": idx,
            "open": 100.0 + (pd.Series(range(240)) * 0.01),
            "high": 101.0 + (pd.Series(range(240)) * 0.01),
            "low": 99.0 + (pd.Series(range(240)) * 0.01),
            "close": 100.5 + (pd.Series(range(240)) * 0.01),
            "volume": 100.0,
        }
    )


class TestResample:
    def test_upsample_1m_to_1h(self, minute_df):
        result = resample(minute_df, Timeframe.H1)
        assert len(result) == 4
        assert "timestamp" in result.columns
        assert "open" in result.columns

    def test_resample_aggregates_correctly(self, minute_df):
        """First bar open = first 1m open, last bar close = last 1m close."""
        result = resample(minute_df, Timeframe.H1)

        first_bar_open = result.loc[0, "open"]
        first_min_open = minute_df.loc[0, "open"]
        assert first_bar_open == first_min_open

        last_bar_close = result.loc[len(result) - 1, "close"]
        last_min_close = minute_df.loc[len(minute_df) - 1, "close"]
        assert last_bar_close == last_min_close

    def test_volume_is_summed(self, minute_df):
        result = resample(minute_df, Timeframe.H1)
        # 60 minutes per hour × 100 volume each
        assert result.loc[0, "volume"] == 6000.0

    def test_empty_df_raises(self):
        with pytest.raises(ValueError, match="empty"):
            resample(pd.DataFrame(), Timeframe.H1)

    def test_downsample_raises(self, minute_df):
        """Resampling 1m → 1m is not upsampling."""
        with pytest.raises(ValueError, match="upsampling"):
            resample(minute_df, Timeframe.M1)

    def test_dataframe_with_datetime_index(self, minute_df):
        df = minute_df.set_index("timestamp")
        result = resample(df, Timeframe.H1)
        # When a DatetimeIndex is used, the method resets it to a column.
        assert "timestamp" in result.columns
