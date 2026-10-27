"""Trading calendars — trading days, sessions, and session boundaries (sec 9).

A calendar answers three questions about a point in time: is the day a trading
day, which intraday session does the timestamp fall in, and how many expected
bars lie between two timestamps. It never invents bars; it only changes how the
library interprets a bar's presence or absence. A missing bar over a weekend,
holiday, or scheduled maintenance break is an expected closure, not a data
quality failure.

Built-in calendars:

- ``AlwaysOpenCalendar`` — 24/7 (crypto). No sessions.
- ``WeekendClosedCalendar`` — 24/5 with no intraday boundary. The safe default.
- ``SessionCalendar`` — one ``SessionProfile`` per venue, including windows
  that cross midnight (the FX/metals 17:00 New York rollover) and daily
  maintenance breaks.
- ``ExchangeCalendar`` — a weekly session schedule plus holidays and a
  timezone (equities and exchange-traded futures).
- ``LibraryCalendar`` (in ``core.exchange_calendars``) — an optional
  ``exchange_calendars`` backed venue calendar with real holidays and half
  days.

``resolve_calendar`` picks the calendar for an asset class, exchange, and
market type. Exchange holiday lists come from ``exchange_calendars`` when that
optional extra is installed; the interface here is the source of truth, so a
maintained library can populate it without changing this module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from datakodo.core.enums import AssetClass, Session


def _as_date(value: date | datetime) -> date:
    """Normalize a date or datetime to a plain ``date`` (the datetime's date)."""
    return value.date() if isinstance(value, datetime) else value


def _as_utc(value: date | datetime) -> datetime:
    """Normalize a date or datetime to an aware UTC datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return datetime.combine(value, time(0), tzinfo=UTC)


def _zoneinfo(timezone: str) -> ZoneInfo:
    """Resolve an IANA timezone name, raising ``ValueError`` when unknown."""
    try:
        return ZoneInfo(timezone)
    except (KeyError, ValueError):
        raise ValueError(f"Unknown timezone: {timezone!r}.") from None


class IntradayAnchor(StrEnum):
    """Where intraday buckets start when no session-open anchor fits.

    ``SESSION_OPEN`` anchors every bucket at the first tradable minute of the
    session. ``UTC_MIDNIGHT`` and ``LOCAL_MIDNIGHT`` anchor buckets to clock
    boundaries instead, so a 1h bar can start at 09:00 even when a session
    opens at 09:30. The expected count is still clipped to the session, so the
    first and last buckets remain complete relative to the data they cover.
    """

    UTC_MIDNIGHT = "utc_midnight"
    LOCAL_MIDNIGHT = "local_midnight"
    SESSION_OPEN = "session_open"


class TradingDateRule(StrEnum):
    """Which local date labels a window that crosses midnight.

    ``OPEN_DATE`` labels the session by the local day it opens on (equities).
    ``CLOSE_DATE`` labels it by the local day it closes on (the FX 17:00
    rollover, where Monday's session opens Sunday evening).
    """

    OPEN_DATE = "open_date"
    CLOSE_DATE = "close_date"


@dataclass(frozen=True)
class SessionWindow:
    """One labelled window in a venue's local wall-clock time.

    ``start_day_offset`` and ``end_day_offset`` are whole local days relative
    to the window's base date. A window crossing midnight (FX 17:00 to the
    next 17:00) is ``start=17:00, end=17:00, end_day_offset=1``; a 23 hour
    futures window (17:00 previous day to 16:00 current day) is
    ``start=17:00, end=16:00, end_day_offset=1``. Explicit offsets avoid the
    ambiguity of ``end < start``, which cannot distinguish a rollover from a
    zero-length window.
    """

    session: Session | None
    start: time
    end: time
    start_day_offset: int = 0
    end_day_offset: int = 0

    def __post_init__(self) -> None:
        if self.session is not None and not isinstance(self.session, Session):
            object.__setattr__(self, "session", Session(self.session))
        if self.start_day_offset > self.end_day_offset:
            raise ValueError("SessionWindow start_day_offset must not exceed end_day_offset.")
        reference = date(2000, 1, 3)  # a Monday, only used to compare wall clocks
        start = datetime.combine(reference + timedelta(days=self.start_day_offset), self.start)
        end = datetime.combine(reference + timedelta(days=self.end_day_offset), self.end)
        if start >= end:
            raise ValueError("SessionWindow must have a positive duration (start before end).")


@dataclass(frozen=True)
class SessionProfile:
    """A recurring trading window (or windows) for one venue or asset class.

    The legacy single-window form takes ``open`` and ``close`` and optional
    ``starts_previous_day``. The multi-window form takes ``session_windows``
    with explicit day offsets, which models exchanges with a lunch break or
    several daily sessions. The two forms are mutually exclusive.

    ``breaks`` lists same-day local ``(start, end)`` maintenance windows
    subtracted from every nominal window. They are the reason
    ``WeekendClosedCalendar`` cannot model FX: that calendar has no intraday
    boundary to break.

    ``anchor`` controls where intraday buckets begin. ``trading_date_rule``
    controls which local date labels a window that crosses midnight; the
    legacy ``starts_previous_day`` form defaults to ``CLOSE_DATE`` so a Monday
    forex bar is still labelled Monday while opening Sunday evening.

    Times are local wall-clock times in ``timezone``; DST is resolved by the
    timezone database, so a 23 or 25 hour local day produces the matching UTC
    window automatically.
    """

    timezone: str
    open: time | None = None
    close: time | None = None
    session: Session | None = None
    weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})
    starts_previous_day: bool = False
    breaks: tuple[tuple[time, time], ...] = ()
    session_windows: tuple[SessionWindow, ...] = ()
    anchor: IntradayAnchor = IntradayAnchor.SESSION_OPEN
    trading_date_rule: TradingDateRule | None = None

    _windows: tuple[SessionWindow, ...] = field(default=(), init=False, repr=False, compare=False)
    _rule: TradingDateRule = field(
        default=TradingDateRule.OPEN_DATE, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        _zoneinfo(self.timezone)

        session = self.session
        if session is not None and not isinstance(session, Session):
            session = Session(session)
            object.__setattr__(self, "session", session)

        anchor = self.anchor
        if not isinstance(anchor, IntradayAnchor):
            anchor = IntradayAnchor(anchor)
            object.__setattr__(self, "anchor", anchor)

        weekdays = frozenset(self.weekdays)
        if not weekdays:
            raise ValueError("SessionProfile.weekdays must not be empty.")
        if any(day < 0 or day > 6 for day in weekdays):
            raise ValueError("SessionProfile.weekdays must be ISO weekdays in 0..6.")
        object.__setattr__(self, "weekdays", weekdays)

        breaks = tuple(self.breaks)
        for break_start, break_end in breaks:
            if break_start >= break_end:
                raise ValueError("SessionProfile breaks must be same-day windows with start < end.")
        object.__setattr__(self, "breaks", breaks)

        windows = tuple(self.session_windows)
        for window in windows:
            if not isinstance(window, SessionWindow):
                raise TypeError(
                    "SessionProfile.session_windows must contain SessionWindow objects."
                )

        has_legacy = self.open is not None or self.close is not None
        if windows:
            if has_legacy:
                raise ValueError(
                    "SessionProfile takes either open/close or session_windows, not both."
                )
            if self.starts_previous_day:
                raise ValueError(
                    "SessionProfile.starts_previous_day is only valid with open/close."
                )
            rule = self.trading_date_rule or TradingDateRule.OPEN_DATE
        else:
            open_time = self.open
            close_time = self.close
            if open_time is None or close_time is None:
                raise ValueError("SessionProfile requires open and close, or session_windows.")
            if self.starts_previous_day:
                # The window wraps past local midnight (close == open means 24h).
                if close_time > open_time:
                    raise ValueError(
                        "SessionProfile with starts_previous_day requires close <= open."
                    )
                windows = (SessionWindow(session, open_time, close_time, 0, 1),)
                rule = self.trading_date_rule or TradingDateRule.CLOSE_DATE
            else:
                if open_time >= close_time:
                    raise ValueError(
                        "SessionProfile requires open < close unless starts_previous_day."
                    )
                windows = (SessionWindow(session, open_time, close_time, 0, 0),)
                rule = self.trading_date_rule or TradingDateRule.OPEN_DATE

        if self.trading_date_rule is not None and not isinstance(
            self.trading_date_rule, TradingDateRule
        ):
            rule = TradingDateRule(self.trading_date_rule)
        object.__setattr__(self, "_windows", windows)
        object.__setattr__(self, "_rule", rule)

        reference = date(2000, 1, 3)
        nominal = self.nominal_windows(reference)
        for previous, current in zip(nominal, nominal[1:]):
            if current[0] < previous[1]:
                raise ValueError("SessionProfile windows must not overlap.")

    @property
    def primary_session(self) -> Session | None:
        """The session label for aggregation, or ``None`` when not singular.

        A profile with one labelled window (or a shared label across windows)
        yields that label. Mixed labels yield ``None`` rather than picking one
        arbitrarily, so a daily bar never claims a session it only partly
        covers.
        """
        if self.session is not None:
            return self.session
        labels = {window.session for window in self._windows if window.session is not None}
        if len(labels) == 1:
            return next(iter(labels))
        return None

    def nominal_windows(
        self,
        day: date,
        *,
        early_close: time | None = None,
    ) -> list[tuple[datetime, datetime]]:
        """The unbroken local windows for trading date ``day``.

        ``day`` is the trading date the session is labelled by, per
        ``trading_date_rule``. The returned datetimes are local and aware.
        Breaks are not subtracted; use :meth:`windows` for the tradable
        segments.
        """
        tz = _zoneinfo(self.timezone)
        result: list[tuple[datetime, datetime]] = []
        for window in self._windows:
            if self._rule is TradingDateRule.CLOSE_DATE:
                base = day - timedelta(days=window.end_day_offset)
            else:
                base = day
            start = datetime.combine(
                base + timedelta(days=window.start_day_offset), window.start, tzinfo=tz
            )
            end = datetime.combine(
                base + timedelta(days=window.end_day_offset), window.end, tzinfo=tz
            )
            if early_close is not None:
                candidate = datetime.combine(end.date(), early_close, tzinfo=tz)
                if start < candidate < end:
                    end = candidate
            if end > start:
                result.append((start, end))
        result.sort()
        return result

    def windows(
        self,
        day: date,
        *,
        early_close: time | None = None,
    ) -> list[tuple[datetime, datetime]]:
        """Return the UTC ``[start, end)`` tradable segments for ``day``.

        Breaks are subtracted, so a session becomes several windows when it
        has an intraday maintenance break. ``early_close`` can only move the
        close earlier than the profile's close.
        """
        tz = _zoneinfo(self.timezone)
        result: list[tuple[datetime, datetime]] = []
        for start, end in self.nominal_windows(day, early_close=early_close):
            result.extend(
                (window_start.astimezone(UTC), window_end.astimezone(UTC))
                for window_start, window_end in _subtract_breaks(start, end, self.breaks, tz)
            )
        return result


def _subtract_breaks(
    start: datetime,
    end: datetime,
    breaks: tuple[tuple[time, time], ...],
    tz: ZoneInfo,
) -> list[tuple[datetime, datetime]]:
    """Remove each same-day break interval that falls inside ``[start, end)``."""
    intervals = [(start, end)]
    day = start.date()
    while day <= end.date():
        for break_start, break_end in breaks:
            cut_start = datetime.combine(day, break_start, tzinfo=tz)
            cut_end = datetime.combine(day, break_end, tzinfo=tz)
            if cut_end <= start or cut_start >= end:
                continue
            trimmed: list[tuple[datetime, datetime]] = []
            for window_start, window_end in intervals:
                if cut_start >= window_end or cut_end <= window_start:
                    trimmed.append((window_start, window_end))
                    continue
                if window_start < cut_start:
                    trimmed.append((window_start, cut_start))
                if cut_end < window_end:
                    trimmed.append((cut_end, window_end))
            intervals = trimmed
        day += timedelta(days=1)
    return intervals


def _count_slots(
    windows: Iterable[tuple[datetime, datetime]],
    start: datetime,
    end: datetime,
    step: timedelta,
) -> int:
    """Count source slots anchored at each window start inside ``[start, end)``."""
    step_us = _timedelta_us(step)
    total = 0
    for window_start, window_end in windows:
        lower = max(window_start, start)
        upper = min(window_end, end)
        if lower >= upper:
            continue
        total += _ceil_div(_timedelta_us(upper - window_start), step_us)
        total -= _ceil_div(_timedelta_us(lower - window_start), step_us)
    return total


def _timedelta_us(value: timedelta) -> int:
    """Microseconds in a non-negative timedelta."""
    return value.days * 86_400_000_000 + value.seconds * 1_000_000 + value.microseconds


def _ceil_div(value: int, step: int) -> int:
    """Ceiling division for non-negative integers."""
    return (value + step - 1) // step


class TradingCalendar(ABC):
    """The calendar contract every provider resolves to."""

    @abstractmethod
    def is_trading_day(self, day: date | datetime) -> bool:
        """True when *day* is a trading day (open at least part of the day)."""

    @abstractmethod
    def session_for(self, timestamp: datetime) -> Session | None:
        """The session containing *timestamp*, or ``None`` without sessions."""

    def trading_date_for(self, timestamp: datetime) -> date | None:
        """The trading date containing *timestamp*, or ``None`` when closed.

        The default treats a trading calendar day as a single UTC trading
        date. Calendars with an exchange-local session override this so a
        timestamp is labelled with the local date whose session contains it.
        """
        day = _as_utc(timestamp).date()
        return day if self.is_trading_day(day) else None

    def early_close(self, day: date | datetime) -> time | None:
        """The day's early close in local time, or ``None`` for a normal close.

        Calendars that know exchange holiday schedules override this so the
        resampler can size a shortened session correctly instead of treating
        its missing tail as a gap.
        """
        return None

    def has_trading_day_between(self, start: datetime, end: datetime) -> bool:
        """True when any trading day falls strictly between *start* and *end*."""
        day = _as_date(start) + timedelta(days=1)
        last = _as_date(end)
        while day < last:
            if self.is_trading_day(day):
                return True
            day += timedelta(days=1)
        return False

    def session_windows_between(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Tradable UTC ``[start, end)`` windows clipped to the range.

        The default treats every trading day as one full UTC day. Calendars
        with intraday sessions override this.
        """
        start = _as_utc(start)
        end = _as_utc(end)
        windows: list[tuple[datetime, datetime]] = []
        day = start.date()
        last = end.date()
        while day <= last:
            if self.is_trading_day(day):
                window_start = datetime.combine(day, time(0), tzinfo=UTC)
                window_end = window_start + timedelta(days=1)
                lower = max(window_start, start)
                upper = min(window_end, end)
                if lower < upper:
                    windows.append((lower, upper))
            day += timedelta(days=1)
        return windows

    def expected_bars(self, start: datetime, end: datetime, step: timedelta) -> int:
        """Expected source bars in ``[start, end)`` at session resolution.

        Gap detection compares this count instead of the calendar-day span, so
        a weekend or holiday closure contributes zero expected bars while a
        hole inside an open session contributes the missing slots.
        """
        windows = self.session_windows_between(start, end)
        return _count_slots(windows, _as_utc(start), _as_utc(end), step)


class AlwaysOpenCalendar(TradingCalendar):
    """A 24/7 calendar: every day trades, with no intraday session."""

    def is_trading_day(self, day: date | datetime) -> bool:
        return True

    def session_for(self, timestamp: datetime) -> None:
        return None


class WeekendClosedCalendar(TradingCalendar):
    """A 24/5 calendar: weekdays trade, weekends close, no intraday session."""

    def is_trading_day(self, day: date | datetime) -> bool:
        return _as_date(day).weekday() < 5

    def session_for(self, timestamp: datetime) -> None:
        return None


class ExchangeCalendar(TradingCalendar):
    """A session schedule, holiday set, and timezone for an exchange.

    ``schedule`` maps ISO weekday integers (``date.weekday()``) to the ordered
    session windows for that weekday; windows must be non-overlapping and in
    chronological order. ``holidays`` holds closed dates beyond the weekend.
    ``session_for`` converts the (UTC) timestamp to the exchange's local time
    and returns the window that contains it, or ``None`` on a non-trading day
    or outside every window.
    """

    def __init__(
        self,
        timezone: str,
        schedule: dict[int, tuple[SessionWindow, ...]],
        holidays: Iterable[date] = (),
        early_closes: dict[date, time] | None = None,
    ) -> None:
        self._tz = _zoneinfo(timezone)
        self._schedule = dict(schedule)
        self._holidays = frozenset(holidays)
        self._early_closes = dict(early_closes or {})
        for windows in self._schedule.values():
            for window in windows:
                if not isinstance(window, SessionWindow):
                    raise TypeError("ExchangeCalendar schedule must contain SessionWindow objects.")

    def early_close(self, day: date | datetime) -> time | None:
        """The override close for *day*, or ``None`` for the normal close."""
        return self._early_closes.get(_as_date(day))

    def is_trading_day(self, day: date | datetime) -> bool:
        day = _as_date(day)
        return day.weekday() in self._schedule and day not in self._holidays

    def _windows_for_local_date(self, day: date) -> list[tuple[datetime, datetime, Session | None]]:
        """Absolute local windows whose base date is *day*."""
        windows: list[tuple[datetime, datetime, Session | None]] = []
        for window in self._schedule.get(day.weekday(), ()):
            start = datetime.combine(
                day + timedelta(days=window.start_day_offset), window.start, tzinfo=self._tz
            )
            end = datetime.combine(
                day + timedelta(days=window.end_day_offset), window.end, tzinfo=self._tz
            )
            early = self._early_closes.get(end.date())
            if early is not None:
                candidate = datetime.combine(end.date(), early, tzinfo=self._tz)
                if start < candidate < end:
                    end = candidate
            if end > start:
                windows.append((start, end, window.session))
        windows.sort()
        return windows

    def trading_date_for(self, timestamp: datetime) -> date | None:
        """The local date whose session contains *timestamp* (or ``None``)."""
        local = _as_utc(timestamp).astimezone(self._tz)
        local_day = local.date()
        for day in (local_day - timedelta(days=1), local_day, local_day + timedelta(days=1)):
            if day in self._holidays:
                continue
            for start, end, _ in self._windows_for_local_date(day):
                if start <= local < end:
                    return day
        return None

    def session_for(self, timestamp: datetime) -> Session | None:
        local = _as_utc(timestamp).astimezone(self._tz)
        local_day = local.date()
        for day in (local_day - timedelta(days=1), local_day, local_day + timedelta(days=1)):
            if day in self._holidays:
                continue
            for start, end, session in self._windows_for_local_date(day):
                if start <= local < end:
                    return session
        return None

    def session_windows_between(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        start = _as_utc(start)
        end = _as_utc(end)
        first = start.astimezone(self._tz).date() - timedelta(days=2)
        last = end.astimezone(self._tz).date() + timedelta(days=1)
        windows: list[tuple[datetime, datetime]] = []
        day = first
        while day <= last:
            if day not in self._holidays:
                for window_start, window_end, _ in self._windows_for_local_date(day):
                    lower = max(window_start, start)
                    upper = min(window_end, end)
                    if lower < upper:
                        windows.append((lower, upper))
            day += timedelta(days=1)
        return sorted(windows)


class SessionCalendar(TradingCalendar):
    """A calendar built from a ``SessionProfile``.

    Unlike ``WeekendClosedCalendar`` it knows a real trading-day boundary
    (17:00 New York for forex and metals), a daily maintenance break, and an
    intraday anchor. Holidays and early closes are supplied explicitly.
    """

    def __init__(
        self,
        profile: SessionProfile,
        holidays: Iterable[date] = (),
        early_closes: dict[date, time] | None = None,
        name: str = "",
    ) -> None:
        self.profile = profile
        self._holidays = frozenset(holidays)
        self._early_closes = dict(early_closes or {})
        self.name = name or "SessionCalendar"

    def early_close(self, day: date | datetime) -> time | None:
        return self._early_closes.get(_as_date(day))

    def is_trading_day(self, day: date | datetime) -> bool:
        day = _as_date(day)
        return day.weekday() in self.profile.weekdays and day not in self._holidays

    def _windows_for_day(
        self, day: date
    ) -> tuple[list[tuple[datetime, datetime]], list[tuple[datetime, datetime]]]:
        early = self.early_close(day)
        return (
            self.profile.windows(day, early_close=early),
            self.profile.nominal_windows(day, early_close=early),
        )

    def session_for(self, timestamp: datetime) -> Session | None:
        ts = _as_utc(timestamp)
        local_day = ts.astimezone(_zoneinfo(self.profile.timezone)).date()
        for offset in (0, -1, 1, -2, 2):
            day = local_day + timedelta(days=offset)
            if not self.is_trading_day(day):
                continue
            windows, _ = self._windows_for_day(day)
            for window_start, window_end in windows:
                if window_start <= ts < window_end:
                    return self.profile.primary_session
        return None

    def session_windows_between(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        start = _as_utc(start)
        end = _as_utc(end)
        local = _zoneinfo(self.profile.timezone)
        first = start.astimezone(local).date() - timedelta(days=2)
        last = end.astimezone(local).date() + timedelta(days=2)
        windows: list[tuple[datetime, datetime]] = []
        day = first
        while day <= last:
            if self.is_trading_day(day):
                day_windows, _ = self._windows_for_day(day)
                for window_start, window_end in day_windows:
                    lower = max(window_start, start)
                    upper = min(window_end, end)
                    if lower < upper:
                        windows.append((lower, upper))
            day += timedelta(days=1)
        return sorted(windows)

    def nominal_windows_for(self, day: date) -> list[tuple[datetime, datetime]]:
        """Unbroken UTC nominal windows for trading date *day*."""
        _, nominal = self._windows_for_day(day)
        return [(start.astimezone(UTC), end.astimezone(UTC)) for start, end in nominal]

    def trading_date_for(self, timestamp: datetime) -> date | None:
        ts = _as_utc(timestamp)
        local_day = ts.astimezone(_zoneinfo(self.profile.timezone)).date()
        for offset in (0, -1, 1, -2, 2):
            day = local_day + timedelta(days=offset)
            if not self.is_trading_day(day):
                continue
            windows, _ = self._windows_for_day(day)
            if any(window_start <= ts < window_end for window_start, window_end in windows):
                return day
        return None


# Canonical session profiles. They are plain constants so adapters and users
# can share exactly the same trading-day boundary.

CRYPTO_UTC = SessionProfile(
    timezone="UTC",
    open=time(0),
    close=time(0),
    starts_previous_day=True,
    weekdays=frozenset(range(7)),
    anchor=IntradayAnchor.UTC_MIDNIGHT,
    trading_date_rule=TradingDateRule.CLOSE_DATE,
)
"""24/7 UTC windows. Crypto resolves to ``AlwaysOpenCalendar``; this profile
is for callers that want an explicit 24/7 session grid."""

FX_NY17 = SessionProfile(
    timezone="America/New_York",
    open=time(17),
    close=time(17),
    starts_previous_day=True,
    breaks=((time(17), time(18)),),
    anchor=IntradayAnchor.SESSION_OPEN,
    trading_date_rule=TradingDateRule.CLOSE_DATE,
)
"""Forex 17:00 New York rollover with the 17:00-18:00 maintenance break."""

METALS_NY17 = SessionProfile(
    timezone="America/New_York",
    open=time(17),
    close=time(17),
    starts_previous_day=True,
    breaks=((time(17), time(18)),),
    anchor=IntradayAnchor.SESSION_OPEN,
    trading_date_rule=TradingDateRule.CLOSE_DATE,
)
"""Metals 17:00 New York rollover with the same daily maintenance break."""

XNYS_REGULAR = SessionProfile(
    timezone="America/New_York",
    open=time(9, 30),
    close=time(16),
    session=Session.REGULAR,
    anchor=IntradayAnchor.SESSION_OPEN,
    trading_date_rule=TradingDateRule.OPEN_DATE,
)
"""The New York Stock Exchange regular session (pre/post are venue extras)."""

CME_GLOBEX = SessionProfile(
    timezone="America/Chicago",
    open=time(17),
    close=time(16),
    starts_previous_day=True,
    anchor=IntradayAnchor.SESSION_OPEN,
    trading_date_rule=TradingDateRule.CLOSE_DATE,
)
"""CME Globex: 17:00 previous day to 16:00 current day, Chicago time."""


_EXCHANGE_ALIASES = {
    "NYSE": "XNYS",
    "NASDAQ": "XNAS",
    "ARCA": "ARCX",
    "NYSEARCA": "ARCX",
    "BATS": "BATS",
    "CME": "CMES",
    "GLOBEX": "CMES",
    "CBOT": "XCBT",
    "COMEX": "XCEC",
    "NYMEX": "XNYM",
}
_FUTURES_EXCHANGES = frozenset({"CMES", "XCBT", "XCEC", "XNYM", "XEUR", "GLOBEX"})
_FUTURES_MARKET_TYPES = frozenset({"future", "futures", "cfd"})


def _library_calendar(name: str, session: Session | None = None):
    """Build an ``exchange_calendars`` backed calendar, or ``None``."""
    try:
        from datakodo.core.exchange_calendars import get_library_calendar
    except ImportError:  # pragma: no cover - pandas is a core dependency
        return None
    return get_library_calendar(name, session=session)


def resolve_calendar(
    asset_class: AssetClass,
    exchange: str = "",
    market_type: str = "",
) -> TradingCalendar:
    """Select the calendar for an asset class, exchange, and market type.

    Crypto trades 24/7. Forex and metals use their 17:00 New York rollover.
    Futures use the CME Globex profile (or an ``exchange_calendars`` venue
    when installed). Other instruments with a recognised exchange use that
    venue's ``exchange_calendars`` calendar when the optional extra is
    installed, and fall back to ``WeekendClosedCalendar`` otherwise.
    """
    venue = (exchange or "").strip().upper()
    venue = _EXCHANGE_ALIASES.get(venue, venue)
    market = (market_type or "").strip().lower()

    if asset_class == AssetClass.CRYPTO:
        return AlwaysOpenCalendar()
    if asset_class == AssetClass.FOREX:
        return SessionCalendar(FX_NY17, name="FX_NY17")
    if asset_class == AssetClass.METAL:
        return SessionCalendar(METALS_NY17, name="METALS_NY17")

    if market in _FUTURES_MARKET_TYPES or venue in _FUTURES_EXCHANGES:
        return _library_calendar(venue or "CMES", session=None) or SessionCalendar(
            CME_GLOBEX, name="CME_GLOBEX"
        )

    if venue:
        return _library_calendar(venue) or WeekendClosedCalendar()
    return WeekendClosedCalendar()
