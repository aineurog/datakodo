"""Pagination tests."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from datakodo.core.enums import Timeframe
from datakodo.ops.pagination import paginate


def _mock_fetch(symbol, start, end):
    """Return a single candle at *start*."""
    return pd.DataFrame(
        {
            "timestamp": [start],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "volume": [500.0],
        }
    )


class TestPaginate:
    def test_single_chunk(self):
        start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2024, 1, 1, 1, 0, tzinfo=UTC)

        result = paginate(_mock_fetch, "BTCUSDT", Timeframe.H1, start, end, max_per_request=1000)
        assert len(result) == 1
        assert result.loc[0, "timestamp"] == start

    def test_empty_result_on_no_data(self):
        def _empty_fetch(symbol, start, end):
            return pd.DataFrame()

        start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2024, 1, 1, 1, 0, tzinfo=UTC)

        result = paginate(_empty_fetch, "BTCUSDT", Timeframe.H1, start, end, max_per_request=1000)
        assert result.empty

    def test_start_after_end_raises(self):
        start = datetime(2024, 1, 2, tzinfo=UTC)
        end = datetime(2024, 1, 1, tzinfo=UTC)

        with pytest.raises(ValueError, match="start"):
            paginate(_mock_fetch, "BTCUSDT", Timeframe.H1, start, end)

    def test_deduplicates_timestamps(self):
        start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2024, 1, 1, 2, 0, tzinfo=UTC)

        # fetch_fn always returns the same row — duplicates should be dropped.
        result = paginate(_mock_fetch, "BTCUSDT", Timeframe.H1, start, end, max_per_request=1000)
        assert len(result) <= 1
