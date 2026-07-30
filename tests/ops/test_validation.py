"""Data quality validation tests."""

import pandas as pd
import pytest

from datakodo.ops.validation import validate_ohlcv


class TestValidateOHLCV:
    def test_valid_dataframe_passes(self, sample_ohlcv_df):
        validate_ohlcv(sample_ohlcv_df)

    def test_empty_dataframe_raises(self):
        df = pd.DataFrame()
        with pytest.raises(ValueError, match="empty"):
            validate_ohlcv(df)

    def test_missing_columns_raises(self):
        df = pd.DataFrame({"wrong_col": [1, 2, 3]})
        with pytest.raises(ValueError, match="Missing"):
            validate_ohlcv(df)

    def test_negative_prices_raise(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.loc[2, "open"] = -1.0
        with pytest.raises(ValueError, match="negative"):
            validate_ohlcv(df)

    def test_negative_volume_raises(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.loc[1, "volume"] = -100.0
        with pytest.raises(ValueError, match="negative"):
            validate_ohlcv(df)

    def test_high_below_low_raises(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.loc[3, "high"] = 50.0
        df.loc[3, "low"] = 100.0
        with pytest.raises(ValueError, match="high < low"):
            validate_ohlcv(df)

    def test_non_monotonic_timestamps_raise(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        timestamps = df["timestamp"].tolist()
        timestamps[2], timestamps[3] = timestamps[3], timestamps[2]
        df["timestamp"] = timestamps
        with pytest.raises(ValueError, match="monotonically"):
            validate_ohlcv(df)

    def test_duplicate_timestamps_raise(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        # Make timestamps monotonically increasing, then create a duplicate.
        df = df.sort_values("timestamp")
        df.loc[4, "timestamp"] = df.loc[0, "timestamp"]
        df = df.sort_values("timestamp")
        with pytest.raises(ValueError, match="Duplicate"):
            validate_ohlcv(df)
