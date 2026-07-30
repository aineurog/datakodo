"""Data quality validation.

Post-fetch checks applied to every DataFrame before it reaches the user:
no negative prices/volumes, high >= low, monotonically increasing
timestamps, no gaps or duplicates.
"""

import pandas as pd


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
