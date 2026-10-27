"""Data quality validation and the fetch contract.

Post-fetch checks applied to every frame before it reaches the user:
finite OHLCV values, no negative prices or volumes, consistent OHLC, strictly
increasing unique timestamps, and an accurate ``is_closed`` flag. Validation
failures raise ``DataValidationError`` carrying the offending timestamp range
(design doc sec 18).

``apply_fetch_contract`` also enforces the canonical ``[start, end)`` request
window: a request that ends inside a forming bar excludes that bar, and only
closed bars are returned by default.
"""

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from datakodo.core.calendar import SessionProfile, TradingCalendar
from datakodo.core.enums import Timeframe
from datakodo.core.exceptions import DataValidationError
from datakodo.core.timeframe import timeframe_delta

logger = logging.getLogger(__name__)

_PRICE_COLUMNS = ("open", "high", "low", "close")
_VALUE_COLUMNS = _PRICE_COLUMNS + ("volume",)
_PERIOD_TIMEFRAMES = frozenset({Timeframe.D1, Timeframe.W1, Timeframe.MN1})


def bar_close_times(
    timestamps: pd.Series | pd.DatetimeIndex,
    timeframe: Timeframe | str,
    *,
    calendar: TradingCalendar | None = None,
    session: SessionProfile | None = None,
) -> pd.Series:
    """Return the UTC instant at which each bar's period closes.

    ``timestamps`` hold bar **open** times. Without a calendar or session a
    period is a fixed clock interval; months close at the next month start,
    so a February bar opened on 1 February closes on 1 March (not 2 March,
    which the 30-day approximation would give).

    With a calendar (and optionally a session profile) the period is defined
    by the trading calendar instead:

    - Intraday bars close at ``open + interval``, clipped to the end of the
      session that contains them. A 4h bar opening at 13:30 on a 16:00 close
      therefore ends at 16:00, and an hourly bar opening at 15:30 does too.
    - Daily, weekly, and monthly bars close at the **last session end** of
      their trading day, ISO week, or calendar month. A US equity daily bar
      opened at 14:30 UTC closes at 21:00 UTC, not at the next UTC midnight.
      Weekly and monthly bars may open at their period's first session open
      (see :func:`anchor_period_bars`) and still close on the real last
      session of the period.

    The result is aligned with the input index, so it can be compared
    directly against a request bound or a server clock.
    """
    tf = Timeframe(timeframe)
    if isinstance(timestamps, pd.Series):
        index = timestamps.index
        ts = pd.DatetimeIndex(pd.to_datetime(timestamps, utc=True))
    else:
        ts = pd.DatetimeIndex(pd.to_datetime(timestamps, utc=True))
        index = ts
    ts = ts.as_unit("ns")

    if tf is Timeframe.MN1:
        nominal = pd.DatetimeIndex(ts + pd.offsets.MonthBegin(1)).as_unit("ns")
    else:
        nominal = (ts + pd.Timedelta(timeframe_delta(tf))).as_unit("ns")

    if ts.empty or (calendar is None and session is None):
        return pd.Series(nominal, index=index)

    windows = _session_windows(
        ts.min() - pd.Timedelta(days=3),
        ts.max() + pd.Timedelta(days=_period_horizon(tf)),
        session,
        calendar,
    )
    if not windows:
        return pd.Series(nominal, index=index)

    if tf in _PERIOD_TIMEFRAMES:
        closes, _ = _period_bounds(ts, tf, windows, "close")
    else:
        closes = _clip_to_sessions(ts, nominal, windows)
    return pd.Series(closes, index=index)


def anchor_period_bars(
    df: pd.DataFrame,
    timeframe: Timeframe | str,
    *,
    calendar: TradingCalendar | None = None,
    session: SessionProfile | None = None,
) -> pd.DataFrame:
    """Relabel calendar-date daily, weekly, and monthly bars at session open.

    Providers such as Alpaca key period bars by calendar date (a US equity
    daily bar is stamped 00:00 Eastern, which is 05:00 UTC). The canonical
    contract (design doc sec 10) is the bar's **open** time, so those bars
    are moved to the first session open of their trading day, ISO week, or
    month. Intraday bars are returned unchanged.

    When a period has no known session the row is left untouched, a warning
    is logged, and ``attrs["bar_anchor"]`` records that the frame is still
    calendar-date labelled. The frame's ``is_closed`` column is not
    recomputed here; apply the fetch contract afterwards.
    """
    tf = Timeframe(timeframe)
    if tf not in _PERIOD_TIMEFRAMES or df.empty or "timestamp" not in df.columns:
        return df.copy()

    out = df.copy()
    ts = pd.DatetimeIndex(pd.to_datetime(out["timestamp"], utc=True)).as_unit("ns")
    windows = _session_windows(
        ts.min() - pd.Timedelta(days=3),
        ts.max() + pd.Timedelta(days=_period_horizon(tf)),
        session,
        calendar,
    )
    if not windows:
        logger.warning(
            "Cannot anchor %s bars to a session open: no calendar or session windows.",
            tf.value,
        )
        out.attrs["bar_anchor"] = "calendar_date"
        return out

    anchors, matched = _period_bounds(ts, tf, windows, "open")
    out["timestamp"] = anchors
    if not matched.all():
        logger.warning(
            "Could not anchor %d %s bar(s) to a session open; left calendar-date labelled.",
            int((~matched).sum()),
            tf.value,
        )
    out.attrs["bar_anchor"] = "session_open" if matched.all() else "calendar_date"
    return out


def add_is_closed(
    df: pd.DataFrame,
    timeframe: str,
    *,
    now: datetime | None = None,
    calendar: TradingCalendar | None = None,
    session: SessionProfile | None = None,
) -> pd.DataFrame:
    """Append an ``is_closed`` column marking whether each bar is final.

    A bar is closed once its period has ended (design doc sec 10, 18). The
    period end comes from :func:`bar_close_times`: a fixed interval without a
    calendar, or the containing session's close, the trading day's last
    session, or the week's/month's last session when one is supplied. This is
    why an equity daily bar closes at 21:00 UTC rather than at the next UTC
    midnight, and why a February monthly bar closes on 1 March.

    Bars still forming at call time are marked ``False``; every bar is
    ``True`` when the range lies fully in the past. Returns a copy; the input
    is untouched.

    ``now`` defaults to the wall clock, but callers with a server-side clock
    (e.g. MT5's tick time) should pass it explicitly so a drifted PC clock
    can neither drop real closed bars nor keep forming ones.

    Raises ``ValueError`` for an unknown ``timeframe``.
    """
    if "timestamp" not in df.columns:
        return df.assign(is_closed=True)
    now_ts = _to_utc(now) if now is not None else pd.Timestamp.now(tz="UTC")
    ts = pd.to_datetime(df["timestamp"], utc=True)
    closes = bar_close_times(ts, timeframe, calendar=calendar, session=session)
    out = df.copy()
    out["is_closed"] = closes <= now_ts
    return out


def drop_incomplete_bars(
    df: pd.DataFrame,
    timeframe: str,
    *,
    now: datetime | None = None,
    calendar: TradingCalendar | None = None,
    session: SessionProfile | None = None,
) -> pd.DataFrame:
    """Drop bars that are still forming so only fully closed bars remain.

    A bar is closed once its period has ended (design doc sec 18: only
    closed/final data is returned for analysis by default). The period end
    follows :func:`bar_close_times`, so session and calendar periods are
    respected. Any bar still open at call time is removed. Raises
    ``ValueError`` for an unknown ``timeframe``.
    """
    if df.empty or "timestamp" not in df.columns:
        return df
    out = add_is_closed(df, timeframe, now=now, calendar=calendar, session=session)
    closed = out.loc[out["is_closed"]].drop(columns=["is_closed"])
    return closed.reset_index(drop=True)


def apply_fetch_contract(
    df: pd.DataFrame,
    timeframe: str,
    start: datetime,
    end: datetime,
    *,
    now: datetime | None = None,
    closed_only: bool = True,
    allow_negative_prices: bool = False,
    calendar: TradingCalendar | None = None,
    session: SessionProfile | None = None,
) -> pd.DataFrame:
    """Apply the canonical half-open ``[start, end)`` fetch contract.

    A bar is kept only when its open is at or after ``start``, its open is
    before ``end``, and its close is at or before ``end``. The last rule
    excludes a bar that the request ends inside, so a request ending at 12:30
    never returns a 12:00 hourly bar that closes at 13:00.

    Pass ``calendar`` (and ``session``) whenever the provider's periods are
    defined by a trading calendar, not the UTC clock. The close of an equity
    daily bar is then the 21:00 UTC session close, a weekly bar ends with the
    week's last session, and a February monthly bar closes on 1 March. Without
    it, the fixed interval (a month is 30 days) can drop a bar that is already
    final or keep one that is still forming.

    Forming bars are then removed when ``closed_only`` is true, and the result
    is validated.

    An empty result is returned as an empty frame rather than raising; the
    adapter decides whether "no data" is an error.
    """
    if df.empty:
        return df

    start_ts = _to_utc(start)
    end_ts = _to_utc(end)

    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    timestamps = out["timestamp"]
    closes = bar_close_times(timestamps, timeframe, calendar=calendar, session=session)
    keep = (timestamps >= start_ts) & (timestamps < end_ts) & (closes <= end_ts)
    out = out.loc[keep].reset_index(drop=True)
    if out.empty:
        return out

    out = add_is_closed(out, timeframe, now=now, calendar=calendar, session=session)
    if closed_only:
        out = out.loc[out["is_closed"]].reset_index(drop=True)
    if out.empty:
        return out

    validate_ohlcv(out, allow_negative_prices=allow_negative_prices)
    return out


def validate_ohlcv(df: pd.DataFrame, *, allow_negative_prices: bool = False) -> None:
    """Run all quality checks on an OHLCV frame.

    Raises ``DataValidationError`` with the offending timestamp range when a
    check fails (design doc sec 18). Negative prices are rejected unless the
    instrument explicitly opts in (for example a spread product that can
    settle below zero); volume must always be non-negative.
    """
    if df.empty:
        raise DataValidationError("OHLCV frame is empty.")

    required = {"timestamp", "open", "high", "low", "close", "volume", "is_closed"}
    missing = required - set(df.columns)
    if missing:
        raise DataValidationError(f"Missing required columns: {missing}")

    values = df[list(_VALUE_COLUMNS)].to_numpy(dtype="float64")
    finite = np.isfinite(values).all(axis=1)
    if not finite.all():
        raise _range_error("OHLCV contains NaN or infinite values", df.loc[~finite])

    for column in _PRICE_COLUMNS:
        negative = df[column] < 0
        if not allow_negative_prices and negative.any():
            raise _range_error(f"Column {column!r} contains negative values", df.loc[negative])

    negative_volume = df["volume"] < 0
    if negative_volume.any():
        raise _range_error("Column 'volume' contains negative values", df.loc[negative_volume])

    if (df["high"] < df["low"]).any():
        raise _range_error("Found rows where high < low", df.loc[df["high"] < df["low"]])

    high = df["high"]
    low = df["low"]
    inconsistent = (
        (high < df["open"]) | (high < df["close"]) | (low > df["open"]) | (low > df["close"])
    )
    if inconsistent.any():
        raise _range_error(
            "Found rows where OHLC is inconsistent (high must bound open/close and "
            "low must bound open/close)",
            df.loc[inconsistent],
        )

    if not df["timestamp"].is_monotonic_increasing:
        timestamps = df["timestamp"]
        out_of_order = timestamps <= timestamps.shift(1)
        raise _range_error("Timestamps are not monotonically increasing", df.loc[out_of_order])

    duplicates = df["timestamp"].duplicated()
    if duplicates.any():
        raise _range_error("Duplicate timestamps found", df.loc[duplicates])


def detect_gaps(
    df: pd.DataFrame,
    timeframe: str,
    calendar: TradingCalendar | None = None,
) -> pd.DataFrame:
    """Return rows where expected source bars are missing before the next bar.

    Expects a sorted, deduplicated OHLCV frame. Without a calendar every hole
    larger than one candle is flagged, preserving the original behavior.

    With a calendar, the gap is sized by ``calendar.expected_bars``: the
    number of source bars the open sessions actually expect between the two
    timestamps. A weekend, holiday, or maintenance break therefore
    contributes zero expected bars and is suppressed, while a hole inside an
    open session is flagged with the exact number of missing bars. This
    replaces counting calendar days, which missed partial trading days and
    miscounted holidays.

    Returns:
        A DataFrame of rows that precede a gap, with an extra ``gap_missing``
        column (number of missing source bars between the current and next
        row). Empty if no gaps are found.

    Design doc sec 18: gap detection catches provider-side bugs such as
    missing candles, exchange downtime, or data feed interruption; a calendar
    (design doc sec 9) turns expected weekend and holiday closures into
    silence instead of a false alarm.
    """
    if df.empty or "timestamp" not in df.columns or len(df) < 2:
        return df.iloc[:0].assign(gap_missing=0)

    delta = timeframe_delta(timeframe)
    step = pd.Timedelta(delta)
    ts = df["timestamp"]
    intervals = ts.shift(-1) - ts
    candidates = (intervals > step).to_numpy(copy=True)
    candidates[-1] = False
    missing = np.zeros(len(df), dtype=np.int64)

    if calendar is None:
        for index in np.flatnonzero(candidates):
            missing[index] = int(round(intervals.iloc[index] / step) - 1)
    else:
        for index in np.flatnonzero(candidates):
            expected = calendar.expected_bars(ts.iloc[index], ts.iloc[index + 1], delta)
            if expected <= 1:
                candidates[index] = False
            else:
                missing[index] = expected - 1

    result = df.loc[candidates].copy()
    if result.empty:
        return result.assign(gap_missing=0)
    result["gap_missing"] = missing[candidates]
    return result


def _period_horizon(timeframe: Timeframe) -> int:
    """Days of look-ahead needed to see a period's final session."""
    if timeframe is Timeframe.MN1:
        return 40
    if timeframe is Timeframe.W1:
        return 10
    return 3


def _session_windows(
    start: pd.Timestamp,
    end: pd.Timestamp,
    session: SessionProfile | None,
    calendar: TradingCalendar | None,
) -> list[tuple[pd.Timestamp, pd.Timestamp, date, pd.Timestamp]]:
    """UTC tradable windows overlapping ``[start, end)`` with their trading date.

    Each entry is ``(window_start, window_end, trading_date, nominal_open)``.
    The window bounds exclude maintenance breaks; ``nominal_open`` is the
    profile's session open, which the resampler also uses as a period anchor,
    so a leading break does not shift the bar's label.

    A session profile supplies the intraday window shape (including breaks
    and early closes); the calendar supplies holidays. When only a calendar
    is given its own windows are used, which is correct for venues whose
    calendar already carries session times.
    """
    start = _to_utc(start)
    end = _to_utc(end)
    if session is not None:
        timezone = ZoneInfo(session.timezone)
        first = (start - pd.Timedelta(days=3)).tz_convert(timezone).date()
        last = (end + pd.Timedelta(days=3)).tz_convert(timezone).date()
        result: list[tuple[pd.Timestamp, pd.Timestamp, date, pd.Timestamp]] = []
        day = first
        while day <= last:
            tradeable = day.weekday() in session.weekdays
            if tradeable and calendar is not None and not calendar.is_trading_day(day):
                tradeable = False
            if tradeable:
                early = calendar.early_close(day) if calendar is not None else None
                nominal = session.nominal_windows(day, early_close=early)
                if not nominal:
                    day += timedelta(days=1)
                    continue
                nominal_open = min(pd.Timestamp(window[0]).tz_convert("UTC") for window in nominal)
                for window_start, window_end in session.windows(day, early_close=early):
                    lower = pd.Timestamp(window_start).tz_convert("UTC")
                    upper = pd.Timestamp(window_end).tz_convert("UTC")
                    if lower < end and upper > start:
                        result.append((lower, upper, day, nominal_open))
            day += timedelta(days=1)
        result.sort(key=lambda item: item[0])
        return result

    if calendar is None:
        return []
    result = []
    for window_start, window_end in calendar.session_windows_between(start, end):
        lower = pd.Timestamp(window_start).tz_convert("UTC")
        upper = pd.Timestamp(window_end).tz_convert("UTC")
        trading_day = calendar.trading_date_for(lower)
        if trading_day is not None and lower < end and upper > start:
            result.append((lower, upper, trading_day, lower))
    return result


def _period_key(day: date, timeframe: Timeframe) -> tuple[int, ...]:
    if timeframe is Timeframe.D1:
        return (day.toordinal(),)
    if timeframe is Timeframe.W1:
        iso = day.isocalendar()
        return (iso.year, iso.week)
    return (day.year, day.month)


def _period_bounds(
    ts: pd.DatetimeIndex,
    timeframe: Timeframe,
    windows: list[tuple[pd.Timestamp, pd.Timestamp, date, pd.Timestamp]],
    bound: Literal["open", "close"],
) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """First open or last close of the session group each bar belongs to.

    A bar whose timestamp falls between sessions (for example a daily bar
    keyed to local midnight, before that day's session opens) belongs to the
    next session. The returned mask is ``False`` only when no session follows
    the timestamp at all, so the caller can leave that row untouched.
    """
    starts = np.array([window[0].value for window in windows], dtype=np.int64)
    ends = np.array([window[1].value for window in windows], dtype=np.int64)
    values = ts.asi8
    position = np.searchsorted(starts, values, side="right") - 1
    safe = np.clip(position, 0, len(windows) - 1)
    inside = (position >= 0) & (values < ends[safe])
    chosen = np.where(inside, position, position + 1)
    matched = chosen < len(windows)

    bounds: dict[tuple[int, ...], int] = {}
    for index, (_, window_end, trading_day, nominal_open) in enumerate(windows):
        key = _period_key(trading_day, timeframe)
        value = nominal_open.value if bound == "open" else window_end.value
        current = bounds.get(key)
        if current is None or (value < current if bound == "open" else value > current):
            bounds[key] = value

    result = values.copy()
    for row, window_index in enumerate(chosen):
        if not matched[row]:
            continue
        key = _period_key(windows[int(window_index)][2], timeframe)
        result[row] = bounds[key]
    return pd.to_datetime(result, unit="ns", utc=True), matched


def _clip_to_sessions(
    ts: pd.DatetimeIndex,
    nominal: pd.DatetimeIndex,
    windows: list[tuple[pd.Timestamp, pd.Timestamp, date, pd.Timestamp]],
) -> pd.DatetimeIndex:
    """Shorten a bar that opens inside a session so it ends with that session."""
    starts = np.array([window[0].value for window in windows], dtype=np.int64)
    ends = np.array([window[1].value for window in windows], dtype=np.int64)
    values = ts.asi8
    position = np.searchsorted(starts, values, side="right") - 1
    safe = np.clip(position, 0, len(windows) - 1)
    inside = (position >= 0) & (values < ends[safe])
    result = nominal.asi8.copy()
    result[inside] = np.minimum(result[inside], ends[safe[inside]])
    return pd.to_datetime(result, unit="ns", utc=True)


def _to_utc(value: datetime) -> pd.Timestamp:
    """Normalize a request bound to a UTC timestamp; naive means UTC."""
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(UTC)
    return timestamp.tz_convert(UTC)


def _range_error(message: str, rows: pd.DataFrame) -> DataValidationError:
    """Build a ``DataValidationError`` carrying the offending timestamp range."""
    if rows.empty or "timestamp" not in rows.columns:
        return DataValidationError(message)
    timestamps = pd.to_datetime(rows["timestamp"], utc=True)
    return DataValidationError(
        f"{message} ({len(rows)} row(s), timestamps "
        f"{timestamps.iloc[0]} .. {timestamps.iloc[-1]}).",
        start=timestamps.iloc[0].to_pydatetime(),
        end=timestamps.iloc[-1].to_pydatetime(),
        count=len(rows),
    )
