"""Output format conversion tests (design doc sec 12)."""

import pandas as pd
import pytest

from datakodo.ops.output import SUPPORTED_OUTPUT_FORMATS, to_output_format


@pytest.fixture
def ohlcv_df() -> pd.DataFrame:
    """A tiny canonical OHLCV frame."""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2024-01-01 00:00:00", "2024-01-01 01:00:00"], utc=True
            ),
            "open": [100.0, 101.0],
            "high": [110.0, 111.0],
            "low": [90.0, 91.0],
            "close": [105.0, 106.0],
            "volume": [1000.0, 2000.0],
            "session": ["n/a", "n/a"],
        }
    )


class TestToOutputFormat:
    def test_pandas_passthrough(self, ohlcv_df):
        out = to_output_format(ohlcv_df, "pandas")
        assert out is ohlcv_df

    def test_default_is_pandas(self, ohlcv_df):
        out = to_output_format(ohlcv_df)
        assert out is ohlcv_df

    def test_polars(self, ohlcv_df):
        out = to_output_format(ohlcv_df, "polars")
        assert type(out).__name__ == "DataFrame"  # polars.DataFrame
        assert out.height == 2
        assert out["close"].to_list() == [105.0, 106.0]

    def test_arrow(self, ohlcv_df):
        out = to_output_format(ohlcv_df, "arrow")
        assert type(out).__name__ == "Table"  # pyarrow.Table
        assert out.num_rows == 2
        assert out.num_columns == 7

    def test_numpy(self, ohlcv_df):
        out = to_output_format(ohlcv_df, "numpy")
        assert type(out).__name__ == "ndarray"
        assert out.shape == (2, 7)

    def test_unsupported_format_raises(self, ohlcv_df):
        with pytest.raises(ValueError, match="Unsupported output format"):
            to_output_format(ohlcv_df, "csv")

    def test_supported_formats_listed(self):
        assert SUPPORTED_OUTPUT_FORMATS == ("pandas", "polars", "arrow", "numpy")
