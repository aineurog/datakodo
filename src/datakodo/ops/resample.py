"""Session aware timeframe resampling — derive larger timeframes from smaller ones.

One implementation serves all providers. Only upsampling is valid; the source
timeframe must be stated explicitly so the expected source grid is never
inferred from the data (a gappy feed cannot masquerade as a coarser one).

Buckets are built on a session grid, not on pandas clock offsets:

- With a :class:`~datakodo.core.calendar.SessionProfile`, every bucket is
  anchored at the session open and never spans a session, a weekend, a
  holiday, or an intraday maintenance break. A daily bar for a 17:00 New York
  forex rollover covers Sunday 17:00 through Monday 17:00 exactly.
- Without a profile, a day is a UTC calendar day on a trading day.
- Weekly and monthly bars are anchored at the first session open of the
  period, and their close timestamp is the last session close.

Each output bar carries ``source_count``, ``expected_count``, ``is_complete``,
``is_partial``, and ``is_closed`` so a caller can tell a real gap from a
boundary truncation (design doc sec 8, 10, 18).
"""

from __future__ import annotations

import logging
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

import numpy as np
import pandas as pd

from datakodo.core.calendar import IntradayAnchor, SessionProfile, TradingCalendar
from datakodo.core.enums import Timeframe
from datakodo.core.exceptions import DataValidationError
from datakodo.core.timeframe import timeframe_delta

logger = logging.getLogger(__name__)

_TIMEFRAME_ORDER: tuple[Timeframe, ...] = (
    Timeframe.M1,
    Timeframe.M5,
    Timeframe.M15,
    Timeframe.M30,
    Timeframe.H1,
    Timeframe.H2,
    Timeframe.H4,
    Timeframe.H8,
    Timeframe.H12,
    Timeframe.D1,
    Timeframe.W1,
    Timeframe.MN1,
)

_INTRADAY: frozenset[Timeframe] = frozenset(
    {
        Timeframe.M1,
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.M30,
        Timeframe.H1,
        Timeframe.H2,
        Timeframe.H4,
        Timeframe.H8,
        Timeframe.H12,
    }
)

# Summed additive columns and the two columns with their own aggregate rule.
_SUM_EXTRAS: tuple[str, ...] = (
    "quote_volume",
    "trades_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "turnover",
    "real_volume",
)
_EXTRA_ORDER: tuple[str, ...] = (
    "quote_volume",
    "trades_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "vwap",
    "turnover",
    "spread",
    "real_volume",
)
_BASE_INPUT: frozenset[str] = frozenset(
    {"timestamp", "open", "high", "low", "close", "volume", "is_closed", "session"}
)


@dataclass(frozen=True)
class _Segment:
    """One continuous tradable window, already in UTC.

    ``nominal_start`` and ``nominal_end`` are the session boundaries before
    maintenance breaks. Daily and period buckets anchor there, so the FX
    daily bar is timestamped at the 17:00 rollover even though the first
    tradable segment begins at 18:00 after the break.
    """

    start: pd.Timestamp
    end: pd.Timestamp
    trading_date: date
    nominal_start: pd.Timestamp
    nominal_end: pd.Timestamp


@dataclass(frozen=True)
class _Bucket:
    """One output bar, spanning a contiguous clock interval."""

    start: pd.Timestamp
    end: pd.Timestamp
    dates: tuple[date, ...]
    segments: tuple[int, ...]


def pick_source_timeframe(target: Timeframe, native: Sequence[Timeframe]) -> Timeframe:
    """Return the largest native timeframe strictly smaller than ``target``.

    This is the finest source DataKodo can fetch to resample up to ``target``
    when the provider does not offer ``target`` natively (design doc sec 8).

    Raises:
        ValueError: If no native timeframe is smaller than ``target`` —
            deriving it would require downsampling, which is not supported.
    """
    target_index = _TIMEFRAME_ORDER.index(target)
    smaller = [tf for tf in native if _TIMEFRAME_ORDER.index(tf) < target_index]
    if not smaller:
        raise ValueError(
            f"Cannot derive {target.value} by resampling: the provider has no "
            f"native timeframe smaller than it."
        )
    return max(smaller, key=_TIMEFRAME_ORDER.index)


def resample(
    df: pd.DataFrame,
    target_timeframe: Timeframe,
    *,
    source_timeframe: Timeframe | str,
    session: SessionProfile | None = None,
    calendar: TradingCalendar | None = None,
    incomplete: Literal["keep", "drop", "raise"] = "keep",
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    """Resample an OHLCV frame to a larger, session anchored timeframe.

    Args:
        df: OHLCV frame with a timezone aware DatetimeIndex or ``timestamp``
            column, in UTC (other zones are converted).
        target_timeframe: Desired output timeframe.
        source_timeframe: Timeframe of the input bars. Required, because the
            expected source grid must be known exactly; inferring it from the
            data makes gaps look like a coarser source.
        session: Optional recurring session window. Buckets are anchored at
            its open and split at its close and breaks.
        calendar: Optional calendar selecting trading days (holidays and
            weekends) and supplying early close overrides.
        incomplete: What to do with bars that are not fully closed:
            ``"keep"`` returns them flagged, ``"drop"`` omits them with a
            warning, ``"raise"`` raises ``DataValidationError``.
        start, end: Optional requested range. They distinguish a bucket cut
            off by the request boundary (``is_partial``) from a bucket with
            missing source bars (``is_complete=False``). Naive values are
            assumed UTC; the range is ``[start, end)``.

    Returns:
        A DataFrame with ``timestamp`` (bucket open), ``close_timestamp``,
        the session label when the profile provides one, OHLCV, the
        completeness columns, and any aggregable optional columns present in
        the input. Only buckets that contain at least one source bar are
        emitted. When the source frame omits ``is_closed``, its bars are
        assumed closed.

    Raises:
        ValueError: For an empty input, missing columns, naive timestamps,
            duplicate timestamps, or a source timeframe that is not smaller
            than the target.
        DataValidationError: When ``incomplete="raise"`` and any output bar is
            not closed.
    """
    if df.empty:
        raise ValueError("Cannot resample an empty DataFrame.")

    source_tf = Timeframe(source_timeframe)
    if _TIMEFRAME_ORDER.index(source_tf) >= _TIMEFRAME_ORDER.index(target_timeframe):
        raise ValueError(
            "Only upsampling is supported. "
            f"Source ({source_tf.value}) must be smaller than "
            f"target ({target_timeframe.value})."
        )
    if incomplete not in ("keep", "drop", "raise"):
        raise ValueError(f"Invalid incomplete mode {incomplete!r}. Use 'keep', 'drop', or 'raise'.")
    missing = {"open", "high", "low", "close", "volume"} - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame is missing required column(s): {sorted(missing)}.")

    work, timestamps = _normalize_input(df)
    # Only an explicit request bound can cut a bucket short. Without one the
    # caller asked for whatever the source holds, so a bucket that starts at a
    # session's nominal open (before an opening maintenance break) is not partial.
    has_request_start = start is not None
    has_request_end = end is not None
    request_start = _to_utc(start) if start is not None else timestamps[0]
    if end is not None:
        request_end = _to_utc(end)
    else:
        request_end = timestamps[-1] + pd.Timedelta(timeframe_delta(source_tf))
    if request_start >= request_end:
        raise ValueError(f"start ({request_start}) must be before end ({request_end}).")

    row_ns = timestamps.asi8
    in_range = (row_ns >= request_start.value) & (row_ns < request_end.value)
    if not in_range.any():
        return _empty_result(session, df.columns)

    first_date, last_date = _segment_date_range(request_start, request_end, target_timeframe)
    segments, by_date = _build_segments(first_date, last_date, session, calendar)
    if not segments:
        return _empty_result(session, df.columns)

    seg_starts = np.array([seg.start.value for seg in segments], dtype=np.int64)
    seg_ends = np.array([seg.end.value for seg in segments], dtype=np.int64)
    row_positions = np.flatnonzero(in_range)
    rows_ns = row_ns[row_positions]
    seg_pos = np.searchsorted(seg_starts, rows_ns, side="right") - 1
    safe_pos = np.clip(seg_pos, 0, len(segments) - 1)
    inside = (seg_pos >= 0) & (rows_ns < seg_ends[safe_pos])
    outside = int((~inside).sum())
    if outside:
        logger.debug("Ignored %d source bar(s) outside every trading session", outside)
    if not inside.any():
        return _empty_result(session, df.columns)

    row_positions = row_positions[inside]
    rows_ns = rows_ns[inside]
    seg_pos = seg_pos[inside]

    buckets = _build_buckets(target_timeframe, segments, by_date, set(seg_pos.tolist()), session)
    bucket_starts = np.array([bucket.start.value for bucket in buckets], dtype=np.int64)
    bucket_ends = np.array([bucket.end.value for bucket in buckets], dtype=np.int64)
    bucket_pos = np.searchsorted(bucket_starts, rows_ns, side="right") - 1
    safe_bucket = np.clip(bucket_pos, 0, len(buckets) - 1)
    in_bucket = (bucket_pos >= 0) & (rows_ns < bucket_ends[safe_bucket])
    if not in_bucket.all():
        logger.debug(
            "Ignored %d source bar(s) outside every output bucket", int((~in_bucket).sum())
        )
    row_positions = row_positions[in_bucket]
    bucket_pos = bucket_pos[in_bucket]

    src = work.iloc[row_positions].copy()
    src["_bucket"] = bucket_pos
    if "is_closed" in src.columns:
        src["_closed"] = src["is_closed"].fillna(False).astype(bool)
    else:
        src["_closed"] = True

    grouped = src.groupby("_bucket", sort=True)
    bucket_ids = np.sort(grouped.size().index.to_numpy())

    aggregates = grouped.agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).reindex(bucket_ids)

    extras: dict[str, pd.Series] = {}
    for column in _SUM_EXTRAS:
        if column in src.columns:
            extras[column] = grouped[column].sum(min_count=1).reindex(bucket_ids)
    if "spread" in src.columns:
        extras["spread"] = grouped["spread"].max().reindex(bucket_ids)
    if "vwap" in src.columns:
        numerator = (src["vwap"] * src["volume"]).groupby(src["_bucket"]).sum(min_count=1)
        denominator = grouped["volume"].sum()
        complete = grouped["vwap"].count() == grouped["vwap"].size()
        extras["vwap"] = (
            (numerator / denominator.where(denominator != 0)).where(complete).reindex(bucket_ids)
        )

    unknown = [
        column
        for column in df.columns
        if column not in _BASE_INPUT and column not in _EXTRA_ORDER and column != "close_timestamp"
    ]
    if unknown:
        logger.debug("Dropped non-canonical column(s) during resampling: %s", sorted(unknown))

    day_anchors: list[int] = []
    week_anchor_map: dict[tuple[int, int], int] = {}
    for trading_date in sorted(by_date):
        anchor = segments[by_date[trading_date][0]].start.value
        day_anchors.append(anchor)
        iso = trading_date.isocalendar()
        week_anchor_map.setdefault((iso.year, iso.week), anchor)
    week_anchors = sorted(week_anchor_map.values())

    expected = np.array(
        [
            _expected_count(
                buckets[index],
                request_start,
                request_end,
                segments,
                source_tf,
                day_anchors,
                week_anchors,
            )
            for index in bucket_ids
        ],
        dtype=np.int64,
    )
    source_count = grouped.size().reindex(bucket_ids).to_numpy(dtype=np.int64)
    complete = source_count >= expected
    partial = np.array(
        [
            (has_request_start and buckets[index].start < request_start)
            or (has_request_end and buckets[index].end > request_end)
            for index in bucket_ids
        ],
        dtype=bool,
    )
    closed = grouped["_closed"].all().reindex(bucket_ids).to_numpy(dtype=bool)
    is_closed = complete & ~partial & closed

    selected = [buckets[index] for index in bucket_ids]
    result: dict[str, object] = {
        "timestamp": pd.DatetimeIndex([bucket.start for bucket in selected]),
        "close_timestamp": pd.DatetimeIndex([bucket.end for bucket in selected]),
    }
    primary_session = session.primary_session if session is not None else None
    if primary_session is not None:
        result["session"] = [primary_session.value] * len(selected)
    result.update(
        {
            "open": aggregates["open"].to_numpy(),
            "high": aggregates["high"].to_numpy(),
            "low": aggregates["low"].to_numpy(),
            "close": aggregates["close"].to_numpy(),
            "volume": aggregates["volume"].to_numpy(),
            "source_count": source_count,
            "expected_count": expected,
            "is_complete": complete,
            "is_partial": partial,
            "is_closed": is_closed,
        }
    )
    for column in _EXTRA_ORDER:
        if column in extras:
            result[column] = extras[column].to_numpy()

    frame = pd.DataFrame(result)

    not_closed = ~frame["is_closed"].to_numpy()
    if not_closed.any():
        if incomplete == "drop":
            dropped = frame.loc[not_closed]
            logger.warning(
                "Dropped %d resampled bar(s) that were incomplete, partial, or still open",
                int(not_closed.sum()),
            )
            frame = frame.loc[~not_closed].reset_index(drop=True)
            # Structured, stateless diagnostics: the caller can see exactly
            # which ranges were omitted without a second return value.
            frame.attrs["resample_diagnostics"] = {
                "dropped_count": len(dropped),
                "dropped_ranges": _contiguous_ranges(dropped),
            }
        elif incomplete == "raise":
            raise DataValidationError(
                f"Resampled frame has {int(not_closed.sum())} bar(s) that are not closed "
                f"({int((~frame['is_complete'].to_numpy()).sum())} incomplete, "
                f"{int(frame['is_partial'].to_numpy().sum())} partial)."
            )

    return frame


def _contiguous_ranges(frame: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Merge adjacent buckets into ``[start, close)`` ranges for diagnostics."""
    ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for start, end in zip(frame["timestamp"], frame["close_timestamp"]):
        start = pd.Timestamp(start)
        end = pd.Timestamp(end)
        if ranges and ranges[-1][1] == start:
            ranges[-1] = (ranges[-1][0], end)
        else:
            ranges.append((start, end))
    return ranges


def _normalize_input(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Return a reset copy of ``df`` sorted by UTC timestamp, plus its timestamps."""
    if isinstance(df.index, pd.DatetimeIndex):
        timestamps = pd.DatetimeIndex(df.index)
        work = df.reset_index(drop=True)
    elif "timestamp" in df.columns and pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        timestamps = pd.DatetimeIndex(df["timestamp"])
        work = df.reset_index(drop=True)
    else:
        raise ValueError("DataFrame must have a DatetimeIndex or a 'timestamp' column.")

    if timestamps.tz is None:
        raise ValueError("OHLCV timestamps must be timezone aware (UTC).")
    # Normalize to nanoseconds: pandas 3 may hold microsecond resolution, while
    # Timestamp.value is always nanoseconds, and the grid compares raw ints.
    timestamps = timestamps.tz_convert("UTC").as_unit("ns")
    if timestamps.duplicated().any():
        raise ValueError("Duplicate timestamps found in the source frame.")

    order = np.argsort(timestamps.asi8, kind="stable")
    timestamps = timestamps[order]
    work = work.iloc[order].reset_index(drop=True)
    work["timestamp"] = timestamps
    return work, timestamps


def _to_utc(value: datetime) -> pd.Timestamp:
    """Normalize a request bound to a UTC timestamp; naive means UTC."""
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _segment_date_range(
    start: pd.Timestamp, end: pd.Timestamp, target: Timeframe
) -> tuple[date, date]:
    """The trading dates whose sessions may contribute to the requested output.

    Two days of slack cover sessions that open on the previous local day
    (forex, futures). Weekly and monthly targets expand to whole periods so
    their first session open and final session close are known.
    """
    first = (start - pd.Timedelta(days=2)).date()
    last = (end + pd.Timedelta(days=2)).date()
    if target is Timeframe.W1:
        first -= timedelta(days=first.weekday())
        last += timedelta(days=6 - last.weekday())
    elif target is Timeframe.MN1:
        first = first.replace(day=1)
        next_month = (last.replace(day=28) + timedelta(days=4)).replace(day=1)
        last = next_month - timedelta(days=1)
    return first, last


def _build_segments(
    first_date: date,
    last_date: date,
    session: SessionProfile | None,
    calendar: TradingCalendar | None,
) -> tuple[list[_Segment], dict[date, list[int]]]:
    """Build the sorted, continuous trading windows over the date span."""
    segments: list[_Segment] = []
    day = first_date
    while day <= last_date:
        trades = True
        if session is not None and day.weekday() not in session.weekdays:
            trades = False
        if trades and calendar is not None and not calendar.is_trading_day(day):
            trades = False
        if trades:
            if session is not None:
                early = calendar.early_close(day) if calendar is not None else None
                nominal = session.nominal_windows(day, early_close=early)
                if nominal:
                    nominal_start = pd.Timestamp(nominal[0][0]).tz_convert("UTC")
                    nominal_end = pd.Timestamp(nominal[-1][1]).tz_convert("UTC")
                    for window_start, window_end in session.windows(day, early_close=early):
                        segments.append(
                            _Segment(
                                pd.Timestamp(window_start).tz_convert("UTC"),
                                pd.Timestamp(window_end).tz_convert("UTC"),
                                day,
                                nominal_start,
                                nominal_end,
                            )
                        )
            else:
                window_start = pd.Timestamp(day, tz="UTC")
                segments.append(
                    _Segment(
                        window_start,
                        window_start + pd.Timedelta(days=1),
                        day,
                        window_start,
                        window_start + pd.Timedelta(days=1),
                    )
                )
        day += timedelta(days=1)

    segments.sort(key=lambda segment: segment.start)
    by_date: dict[date, list[int]] = {}
    for index, segment in enumerate(segments):
        by_date.setdefault(segment.trading_date, []).append(index)
    return segments, by_date


def _build_buckets(
    target: Timeframe,
    segments: list[_Segment],
    by_date: dict[date, list[int]],
    active_segments: set[int],
    session: SessionProfile | None,
) -> list[_Bucket]:
    """Build buckets for the segments that hold at least one source bar."""
    target_delta = pd.Timedelta(timeframe_delta(target))
    if target in _INTRADAY:
        buckets: list[_Bucket] = []
        anchor = session.anchor if session is not None else IntradayAnchor.SESSION_OPEN
        timezone = session.timezone if session is not None else "UTC"
        for segment_index in sorted(active_segments):
            segment = segments[segment_index]
            for bucket_start, bucket_end in _intraday_bucket_bounds(
                segment, target_delta, anchor, timezone
            ):
                buckets.append(
                    _Bucket(
                        bucket_start,
                        bucket_end,
                        (segment.trading_date,),
                        (segment_index,),
                    )
                )
        return buckets

    active_dates = sorted({segments[index].trading_date for index in active_segments})
    if target is Timeframe.D1:
        buckets = []
        for trading_date in active_dates:
            segment_ids = tuple(by_date[trading_date])
            buckets.append(
                _Bucket(
                    min(segments[index].nominal_start for index in segment_ids),
                    max(segments[index].nominal_end for index in segment_ids),
                    (trading_date,),
                    segment_ids,
                )
            )
        return buckets

    def period_key(day: date) -> tuple[int, int]:
        if target is Timeframe.W1:
            iso = day.isocalendar()
            return iso.year, iso.week
        return day.year, day.month

    groups: dict[tuple[int, int], list[date]] = {}
    for trading_date in sorted(by_date):
        groups.setdefault(period_key(trading_date), []).append(trading_date)

    buckets = []
    for key in sorted({period_key(day) for day in active_dates}):
        dates = tuple(groups[key])
        segment_ids = tuple(index for trading_date in dates for index in by_date[trading_date])
        buckets.append(
            _Bucket(
                min(segments[index].nominal_start for index in segment_ids),
                max(segments[index].nominal_end for index in segment_ids),
                dates,
                segment_ids,
            )
        )
    return buckets


def _intraday_bucket_bounds(
    segment: _Segment,
    target_delta: pd.Timedelta,
    anchor: IntradayAnchor,
    timezone: str,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Bucket boundaries for one tradable segment under ``anchor``.

    With a clock anchor the first bucket may start before the segment opens;
    its expected count is clipped to the segment, so it stays complete
    relative to the data it covers and never invents bars.
    """
    if anchor is IntradayAnchor.UTC_MIDNIGHT:
        cursor = segment.start.normalize()
    elif anchor is IntradayAnchor.LOCAL_MIDNIGHT:
        cursor = segment.start.tz_convert(timezone).normalize().tz_convert("UTC")
    else:
        cursor = segment.start

    if anchor is not IntradayAnchor.SESSION_OPEN:
        while cursor + target_delta <= segment.start:
            cursor += target_delta

    bounds: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    while cursor < segment.end:
        bounds.append((cursor, min(cursor + target_delta, segment.end)))
        cursor += target_delta
    return bounds


def _expected_count(
    bucket: _Bucket,
    request_start: pd.Timestamp,
    request_end: pd.Timestamp,
    segments: list[_Segment],
    source: Timeframe,
    day_anchors: list[int],
    week_anchors: list[int],
) -> int:
    """Source bars the bucket should hold, clipped to the requested range."""
    clip_start = max(bucket.start, request_start).value
    clip_end = min(bucket.end, request_end).value
    if clip_start >= clip_end:
        return 0

    if source in _INTRADAY:
        step = pd.Timedelta(timeframe_delta(source)).value
        total = 0
        for index in bucket.segments:
            segment = segments[index]
            lower = max(segment.start.value, clip_start)
            upper = min(segment.end.value, clip_end)
            if lower >= upper:
                continue
            offset_lower = lower - segment.start.value
            offset_upper = upper - segment.start.value
            total += _ceil_div(offset_upper, step) - _ceil_div(offset_lower, step)
        return total

    anchors = day_anchors if source is Timeframe.D1 else week_anchors
    return bisect_left(anchors, clip_end) - bisect_left(anchors, clip_start)


def _ceil_div(value: int, step: int) -> int:
    """Ceiling division for non-negative offsets."""
    return (value + step - 1) // step


def _empty_result(session: SessionProfile | None, columns: Sequence[str]) -> pd.DataFrame:
    """A schema shaped empty result for a range with no tradable source bars."""
    result: dict[str, pd.Series] = {
        "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
        "close_timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
    }
    primary_session = session.primary_session if session is not None else None
    if primary_session is not None:
        result["session"] = pd.Series(dtype=object)
    for column in ("open", "high", "low", "close", "volume"):
        result[column] = pd.Series(dtype="float64")
    result["source_count"] = pd.Series(dtype="int64")
    result["expected_count"] = pd.Series(dtype="int64")
    for column in ("is_complete", "is_partial", "is_closed"):
        result[column] = pd.Series(dtype="bool")
    for column in _EXTRA_ORDER:
        if column in columns:
            result[column] = pd.Series(dtype="float64")
    return pd.DataFrame(result)
