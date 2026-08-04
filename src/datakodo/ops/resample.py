"""Timeframe resampling — derive larger timeframes from smaller ones.

One implementation serves all providers. Only upsampling (1m → 1h)
is valid; downsampling is rejected.
"""

from collections.abc import Sequence

import pandas as pd

from datakodo.core.enums import Timeframe

_TIMEFRAME_MINUTES: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.M30: 30,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
    Timeframe.W1: 10080,
    Timeframe.MN1: 43200,
}


def pick_source_timeframe(
    target: Timeframe, native: Sequence[Timeframe]
) -> Timeframe:
    """Return the largest native timeframe strictly smaller than ``target``.

    This is the finest source DataKodo can fetch to resample up to ``target``
    when the provider does not offer ``target`` natively (design doc sec 7).

    Raises:
        ValueError: If no native timeframe is smaller than ``target`` —
            deriving it would require downsampling, which is not supported.
    """
    target_minutes = _TIMEFRAME_MINUTES[target]
    smaller = [tf for tf in native if _TIMEFRAME_MINUTES[tf] < target_minutes]
    if not smaller:
        raise ValueError(
            f"Cannot derive {target.value} by resampling: the provider has no "
            f"native timeframe smaller than it."
        )
    return max(smaller, key=lambda tf: _TIMEFRAME_MINUTES[tf])


def resample(df: pd.DataFrame, target_timeframe: Timeframe) -> pd.DataFrame:
    """Resample an OHLCV DataFrame to a larger target timeframe.

    Standard OHLCV aggregation rules:
    - open  = first
    - high  = max
    - low   = min
    - close = last
    - volume = sum

    Only upsampling is supported — the source timeframe must be smaller
    than the target. A ValueError is raised for invalid requests.

    Args:
        df: OHLCV DataFrame with a DatetimeIndex or a 'timestamp' column.
        target_timeframe: The desired output Timeframe enum value.

    Returns:
        A new DataFrame resampled to *target_timeframe*.
    """
    if df.empty:
        raise ValueError("Cannot resample an empty DataFrame.")

    target_minutes = _TIMEFRAME_MINUTES[target_timeframe]

    resolved_index = _resolve_index(df)
    if resolved_index is None:
        raise ValueError("DataFrame must have a DatetimeIndex or a 'timestamp' column.")

    source_minutes = _infer_source_minutes(resolved_index)
    if source_minutes >= target_minutes:
        raise ValueError(
            f"Only upsampling is supported. "
            f"Source (~{source_minutes}m) must be smaller than "
            f"target ({target_minutes}m)."
        )

    rule = _to_pandas_freq(target_timeframe)

    # Temporarily use the timestamp as the index for resampling.
    if resolved_index is not df.index:
        df = df.set_index(resolved_index.name)

    result = (
        df.resample(rule)
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )

    if "session" in df.columns:
        # Session is not aggregatable; carry forward the most common label.
        result["session"] = (
            df["session"]
            .resample(rule)
            .agg(lambda x: x.mode().iloc[0] if len(x.mode()) > 0 else x.iloc[0])
        )

    return result.reset_index()


def _resolve_index(df: pd.DataFrame):
    """Return the DatetimeIndex column if available, otherwise None."""
    if isinstance(df.index, pd.DatetimeIndex):
        return df.index
    if "timestamp" in df.columns and pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        return df["timestamp"]
    return None


def _infer_source_minutes(index) -> float:
    """Estimate the source timeframe in minutes from index spacing."""
    if len(index) < 2:
        return 0
    values = pd.Series(index)
    delta = values.diff().median()
    if pd.isna(delta):
        return 0
    return float(delta.total_seconds()) / 60


def _to_pandas_freq(tf: Timeframe) -> str:
    """Map a canonical Timeframe to a pandas frequency string."""
    mapping = {
        Timeframe.M1: "1min",
        Timeframe.M5: "5min",
        Timeframe.M15: "15min",
        Timeframe.M30: "30min",
        Timeframe.H1: "1h",
        Timeframe.H4: "4h",
        Timeframe.D1: "1D",
        Timeframe.W1: "1W",
        Timeframe.MN1: "1ME",
    }
    return mapping[tf]
