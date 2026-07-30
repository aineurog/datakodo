"""Corporate actions adjustment tests."""

import pandas as pd
import pytest

from datakodo.ops.corporate_actions import (
    adjust_ohlcv_for_dividends,
    adjust_ohlcv_for_splits,
)


@pytest.fixture
def ohlcv_df():
    """Small OHLCV DataFrame 2024-01-01 to 2024-01-05, oldest first."""
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=5, freq="1D", tz="UTC"),
            "open": [100.0, 102.0, 104.0, 106.0, 108.0],
            "high": [102.0, 104.0, 106.0, 108.0, 110.0],
            "low": [99.0, 101.0, 103.0, 105.0, 107.0],
            "close": [101.0, 103.0, 105.0, 107.0, 109.0],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0, 1000.0],
        }
    )


class TestAdjustForSplits:
    def test_no_splits_returns_copy(self, ohlcv_df):
        result = adjust_ohlcv_for_splits(ohlcv_df, [])
        assert result.equals(ohlcv_df)
        assert result is not ohlcv_df

    def test_single_split_adjusts_prices(self, ohlcv_df):
        splits = [{"date": "2024-01-04", "ratio": 2.0}]
        result = adjust_ohlcv_for_splits(ohlcv_df, splits)

        # Rows before 2024-01-04 should have prices halved
        assert result.loc[0, "close"] == 50.5
        # Row on/after 2024-01-04 should be unchanged
        assert result.loc[3, "close"] == 107.0

    def test_single_split_adjusts_volume(self, ohlcv_df):
        splits = [{"date": "2024-01-04", "ratio": 2.0}]
        result = adjust_ohlcv_for_splits(ohlcv_df, splits)

        # Volume before split should double
        assert result.loc[0, "volume"] == 2000.0
        # Volume after split should be unchanged
        assert result.loc[3, "volume"] == 1000.0


class TestAdjustForDividends:
    def test_no_dividends_returns_copy(self, ohlcv_df):
        result = adjust_ohlcv_for_dividends(ohlcv_df, [])
        assert result.equals(ohlcv_df)
        assert result is not ohlcv_df

    def test_single_dividend_adjusts_prices(self, ohlcv_df):
        dividends = [{"ex_date": "2024-01-04", "amount": 1.0}]
        result = adjust_ohlcv_for_dividends(ohlcv_df, dividends)

        # Prices before ex-date should be adjusted down
        assert result.loc[0, "close"] < 101.0
        # Prices on/after ex-date unchanged
        assert result.loc[3, "close"] == 107.0
