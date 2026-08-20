"""Timeframe resampling — derive larger timeframes from smaller ones.

One implementation serves all providers. Only upsampling (1m → 1h) is valid;
downsampling is rejected. Periods are calendar-anchored (pandas bins on the
clock/calendar, not a fixed number of rows), and aggregation never merges
across a gap in the source data (design doc sec 8).
"""

import logging
from collections.abc import Sequence

import pandas as pd

from datakodo.core.calendar import TradingCalendar
from datakodo.core.enums import Timeframe

logger = logging.getLogger(__name__)

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


def pick_source_timeframe(target: Timeframe, native: Sequence[Timeframe]) -> Timeframe:
    """Return the largest native timeframe strictly smaller than ``target``.

    This is the finest source DataKodo can fetch to resample up to ``target``
    when the provider does not offer ``target`` natively (design doc sec 8).

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


def resample(
    df: pd.DataFrame,
    target_timeframe: Timeframe,
    calendar: TradingCalendar | None = None,
) -> pd.DataFrame:
    """Resample an OHLCV frame to a larger target timeframe.

    Standard OHLCV aggregation rules:
    - open  = first
    - high  = max
    - low   = min
    - close = last
    - volume = sum

    Only upsampling is supported — the source timeframe must be smaller than
    the target. Periods are calendar-anchored via pandas binning; bars whose
    source period is missing candles (a gap) are dropped rather than silently
    aggregated over the gap. Resampled bars are always fully closed.

    When a ``calendar`` is supplied, output bars whose timestamp falls on a
    non-trading day are dropped (design doc sec 9). This keeps weekly and
    monthly aggregates aligned to trading days rather than the wall clock.

    Args:
        df: OHLCV frame with a DatetimeIndex or a 'timestamp' column.
        target_timeframe: The desired output Timeframe enum value.
        calendar: Optional trading calendar used to drop non-trading-day bars.

    Returns:
        A new DataFrame resampled to *target_timeframe* with an ``is_closed``
        column of all ``True``.
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

    # Use the timestamp as the index for resampling.
    if resolved_index is not df.index:
        df = df.set_index(resolved_index.name)
    df.index.name = "timestamp"

    agg = (
        df.resample(rule, label="left", closed="left")
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

    # Gap awareness: a period is missing candles when it holds fewer source
    # bars than expected. The first and last periods may legitimately be
    # partial (the fetched range does not align to the period boundary), so
    # they are exempt.
    expected = _expected_bars(source_minutes, target_minutes)
    if expected is not None:
        counts = df.resample(rule, label="left", closed="left")["close"].size()
        incomplete = counts[counts < expected]
        if len(counts) > 2:
            incomplete = incomplete.drop(counts.index[[0, -1]], errors="ignore")
        if len(incomplete):
            logger.warning(
                "Dropped %d resampled bars that span gaps in the source data",
                len(incomplete),
            )
            agg = agg.drop(index=incomplete.index, errors="ignore")

    result = agg.copy()
    result["is_closed"] = True

    if "session" in df.columns:
        # Session is not aggregatable; carry forward the most common label.
        session = (
            df["session"]
            .resample(rule, label="left", closed="left")
            .agg(lambda x: x.mode().iloc[0] if len(x.mode()) > 0 else x.iloc[0])
        )
        result["session"] = session.reindex(result.index)

    result = result.reset_index()

    if calendar is not None:
        # Drop output bars anchored on a non-trading day (design doc sec 9).
        keep = result["timestamp"].map(calendar.is_trading_day)
        result = result.loc[keep].reset_index(drop=True)

    return result


def _expected_bars(source_minutes: float, target_minutes: int) -> int | None:
    """Return the number of source bars expected per target period, or None.

    ``None`` when the source spacing cannot be resolved to a whole divisor of
    the target (in which case gap-awareness is skipped rather than guessing).
    """
    if source_minutes <= 0:
        return None
    expected = target_minutes / source_minutes
    if expected != round(expected):
        return None
    return int(round(expected))


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
    """Map a canonical Timeframe to a pandas frequency string.

    Weekly bars anchor on Monday (``"W-MON"``) so the calendar boundary is
    deterministic and documented across providers (design doc sec 8); monthly
    bars anchor on calendar month end (``"ME"``).
    """
    mapping = {
        Timeframe.M1: "1min",
        Timeframe.M5: "5min",
        Timeframe.M15: "15min",
        Timeframe.M30: "30min",
        Timeframe.H1: "1h",
        Timeframe.H4: "4h",
        Timeframe.D1: "1D",
        Timeframe.W1: "W-MON",
        Timeframe.MN1: "ME",
    }
    return mapping[tf]
