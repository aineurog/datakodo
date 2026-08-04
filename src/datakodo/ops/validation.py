"""Data quality validation.

Post-fetch checks applied to every DataFrame before it reaches the user:
no negative prices/volumes, high >= low, monotonically increasing
timestamps, no gaps or duplicates. Also provides helpers to drop bars that
are still forming (design doc sec 17 / 18).
"""

import pandas as pd

from datakodo.core.timeframe import timeframe_delta


def drop_incomplete_bars(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Drop bars that are still forming so only fully closed bars remain.

    A bar whose ``timestamp`` column holds its **open** time is closed once
    ``open_time + interval <= now`` (design doc sec 17: only closed/final
    data is cached and returned for analysis). Any bar still open at call
    time is removed. Raises ``ValueError`` for an unknown ``timeframe``.
    """
    if df.empty or "timestamp" not in df.columns:
        return df
    delta = timeframe_delta(timeframe)
    now = pd.Timestamp.now(tz="UTC")
    ts = df["timestamp"]
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    closed = ts + delta <= now
    return df.loc[closed].copy()


def validate_ohlcv(df: pd.DataFrame) -> None:
    """Run all quality checks on an OHLCV DataFrame.

    Raises ValueError with a descriptive message if any check fails.
    """
    if df.empty:
        raise ValueError("OHLCV DataFrame is empty.")

    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # No negative prices or volumes.
    for col in ("open", "high", "low", "close", "volume"):
        if (df[col] < 0).any():
            raise ValueError(f"Column {col!r} contains negative values.")

    # High must be >= low on every row.
    if (df["high"] < df["low"]).any():
        raise ValueError("Found rows where high < low.")

    # Timestamps must be strictly increasing.
    if not df["timestamp"].is_monotonic_increasing:
        raise ValueError("Timestamps are not monotonically increasing.")

    # No duplicate timestamps.
    if df["timestamp"].duplicated().any():
        raise ValueError("Duplicate timestamps found.")


def detect_gaps(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Return rows where the interval to the next bar exceeds one candle.

    Expects a sorted, deduplicated OHLCV DataFrame. For each row, computes
    the difference to the next row's timestamp; if that gap is larger than
    one candle of ``timeframe``, the row is flagged as a gap boundary.

    Returns:
        A DataFrame of rows that precede a gap, with an extra ``gap_missing``
        column (number of missing candles between the current and next row).
        Empty if no gaps are found.

    Design doc sec 18: gap detection catches provider-side bugs such as
    missing candles, exchange downtime, or data feed interruption.
    """
    if df.empty or "timestamp" not in df.columns or len(df) < 2:
        return df.iloc[:0].assign(gap_missing=0)

    delta = timeframe_delta(timeframe)
    # Interval from each row's timestamp to the next row's timestamp.
    ts = df["timestamp"]
    intervals = ts.shift(-1) - ts
    # A gap exists when that interval is larger than one candle.
    gap_mask = intervals > pd.Timedelta(delta)
    gap_mask.iloc[-1] = False  # last row has no "next row"
    result = df.loc[gap_mask].copy()
    if result.empty:
        return result.assign(gap_missing=0)
    # Number of missing candles = (gap_duration / candle_duration) - 1.
    gap_candles = ((intervals[gap_mask] / pd.Timedelta(delta)).round().astype(int) - 1).values
    result["gap_missing"] = gap_candles
    return result
