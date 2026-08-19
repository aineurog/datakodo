"""Data quality validation.

Post-fetch checks applied to every frame before it reaches the user:
no negative prices/volumes, high >= low, monotonically increasing
timestamps, no gaps or duplicates, and an ``is_closed`` flag on every bar.
Validation failures raise ``DataValidationError`` (design doc sec 18).
"""

import numpy as np
import pandas as pd

from datakodo.core.calendar import TradingCalendar
from datakodo.core.exceptions import DataValidationError
from datakodo.core.timeframe import timeframe_delta


def add_is_closed(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Append an ``is_closed`` column marking whether each bar is final.

    A bar whose ``timestamp`` holds its **open** time is closed once
    ``open_time + interval <= now`` (design doc sec 10, 18). Bars still
    forming at call time are marked ``False``; every bar is ``True`` when the
    range lies fully in the past. Returns a copy; the input is untouched.

    Raises ``ValueError`` for an unknown ``timeframe``.
    """
    if "timestamp" not in df.columns:
        return df.assign(is_closed=True)
    delta = timeframe_delta(timeframe)
    now = pd.Timestamp.now(tz="UTC")
    ts = df["timestamp"]
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    out = df.copy()
    out["is_closed"] = ts + delta <= now
    return out


def drop_incomplete_bars(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Drop bars that are still forming so only fully closed bars remain.

    A bar whose ``timestamp`` column holds its **open** time is closed once
    ``open_time + interval <= now`` (design doc sec 18: only closed/final
    data is returned for analysis by default). Any bar still open at call
    time is removed. Raises ``ValueError`` for an unknown ``timeframe``.
    """
    if df.empty or "timestamp" not in df.columns:
        return df
    out = add_is_closed(df, timeframe)
    closed = out.loc[out["is_closed"]].drop(columns=["is_closed"])
    return closed.reset_index(drop=True)


def validate_ohlcv(df: pd.DataFrame) -> None:
    """Run all quality checks on an OHLCV frame.

    Raises ``DataValidationError`` with a descriptive message if any check
    fails (design doc sec 18).
    """
    if df.empty:
        raise DataValidationError("OHLCV frame is empty.")

    required = {"timestamp", "open", "high", "low", "close", "volume", "is_closed"}
    missing = required - set(df.columns)
    if missing:
        raise DataValidationError(f"Missing required columns: {missing}")

    # No negative prices or volumes.
    for col in ("open", "high", "low", "close", "volume"):
        if (df[col] < 0).any():
            raise DataValidationError(f"Column {col!r} contains negative values.")

    # High must be >= low on every row.
    if (df["high"] < df["low"]).any():
        raise DataValidationError("Found rows where high < low.")

    # Timestamps must be strictly increasing.
    if not df["timestamp"].is_monotonic_increasing:
        raise DataValidationError("Timestamps are not monotonically increasing.")

    # No duplicate timestamps.
    if df["timestamp"].duplicated().any():
        raise DataValidationError("Duplicate timestamps found.")


def detect_gaps(
    df: pd.DataFrame,
    timeframe: str,
    calendar: TradingCalendar | None = None,
) -> pd.DataFrame:
    """Return rows where the interval to the next bar exceeds one candle.

    Expects a sorted, deduplicated OHLCV frame. For each row, computes the
    difference to the next row's timestamp; if that gap is larger than one
    candle of ``timeframe``, the row is flagged as a gap boundary.

    When a ``calendar`` is supplied, a gap that spans no trading day strictly
    between its two bars is treated as a scheduled closure (weekend or holiday)
    and suppressed. Same-day holes are always real, since the market was open
    and a candle is missing. Without a calendar every hole larger than one
    candle is flagged (the previous behavior).

    Returns:
        A DataFrame of rows that precede a gap, with an extra ``gap_missing``
        column (number of missing candles between the current and next row).
        Empty if no gaps are found.

    Design doc sec 18: gap detection catches provider-side bugs such as
    missing candles, exchange downtime, or data feed interruption; a calendar
    (design doc sec 9) turns expected weekend and holiday closures into
    silence instead of a false alarm.
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

    if calendar is not None:
        for i in np.flatnonzero(gap_mask.to_numpy()):
            if _is_scheduled_closure(calendar, ts.iloc[i], ts.iloc[i + 1]):
                gap_mask.iloc[i] = False

    result = df.loc[gap_mask].copy()
    if result.empty:
        return result.assign(gap_missing=0)
    # Number of missing candles = (gap_duration / candle_duration) - 1.
    gap_candles = ((intervals[gap_mask] / pd.Timedelta(delta)).round().astype(int) - 1).values
    result["gap_missing"] = gap_candles
    return result


def _is_scheduled_closure(
    calendar: TradingCalendar,
    prev_ts: pd.Timestamp,
    next_ts: pd.Timestamp,
) -> bool:
    """True when a gap between two bars is fully a weekend or holiday closure.

    A hole within a single day is always a real gap (the market was open), so
    it is never a closure. Across days, the gap is a closure only when no
    trading day lies strictly between the two bar timestamps.
    """
    if prev_ts.date() == next_ts.date():
        return False
    return not calendar.has_trading_day_between(prev_ts, next_ts)
