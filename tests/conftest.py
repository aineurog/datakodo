"""Shared fixtures for all test modules."""

import pandas as pd
import pytest


@pytest.fixture
def sample_ohlcv_df():
    """Return a minimal valid OHLCV DataFrame for testing."""
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC"),
            "open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [102.0, 103.0, 104.0, 105.0, 106.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0],
            "close": [101.0, 102.0, 103.0, 104.0, 105.0],
            "volume": [1000.0, 1100.0, 1200.0, 1300.0, 1400.0],
        }
    )


@pytest.fixture
def sample_ohlcv_df_with_session(sample_ohlcv_df):
    """Return an OHLCV DataFrame with a session column."""
    df = sample_ohlcv_df.copy()
    df["session"] = "n/a"
    return df
