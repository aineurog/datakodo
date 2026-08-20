"""Trading calendars — trading days and session boundaries (design doc sec 9).

A calendar answers two questions about a point in time: is the day a trading
day, and which intraday session does the timestamp fall in. It never invents
bars; it only changes how the library interprets a bar's presence or absence.
A missing bar over a weekend or holiday is an expected closure, not a data
quality failure.

Three built-in calendars cover every current provider:

- ``AlwaysOpenCalendar`` — 24/7 (crypto). No sessions.
- ``WeekendClosedCalendar`` — 24/5 (forex, metals, and the safe default for
  anything not yet given an exchange calendar). No sessions.
- ``ExchangeCalendar`` — a weekly session schedule plus holidays and a
  timezone (equities and exchange-traded futures). Produces real sessions.

``resolve_calendar`` picks the calendar for an asset class. Exchange holiday
lists should come from a maintained library (design doc sec 8); the interface
here is the source of truth and ``ExchangeCalendar`` already accepts an
explicit schedule and holiday set so such a library can populate it without
changing this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from datakodo.core.enums import AssetClass, Session


def _as_date(value: date | datetime) -> date:
    """Normalize a date or datetime to a plain ``date`` (the datetime's date)."""
    return value.date() if isinstance(value, datetime) else value


class TradingCalendar(ABC):
    """The calendar contract every provider resolves to."""

    @abstractmethod
    def is_trading_day(self, day: date | datetime) -> bool:
        """True when *day* is a trading day (open at least part of the day)."""

    @abstractmethod
    def session_for(self, timestamp: datetime) -> Session | None:
        """The session containing *timestamp*, or ``None`` without sessions."""

    def has_trading_day_between(self, start: datetime, end: datetime) -> bool:
        """True when any trading day falls strictly between *start* and *end*.

        This is the primitive gap detection uses to tell a scheduled closure
        (weekend or holiday) from a real missing candle: a gap is a closure
        only when no trading day lies strictly between the two bars.
        """
        day = _as_date(start) + timedelta(days=1)
        last = _as_date(end)
        while day < last:
            if self.is_trading_day(day):
                return True
            day += timedelta(days=1)
        return False


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


@dataclass(frozen=True)
class SessionWindow:
    """A labelled intraday window ``[start, end)`` in the exchange's local time."""

    session: Session
    start: time
    end: time


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
    ) -> None:
        self._tz = ZoneInfo(timezone)
        self._schedule = dict(schedule)
        self._holidays = frozenset(holidays)

    def is_trading_day(self, day: date | datetime) -> bool:
        day = _as_date(day)
        return day.weekday() in self._schedule and day not in self._holidays

    def session_for(self, timestamp: datetime) -> Session | None:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        local = timestamp.astimezone(self._tz)
        if local.date() in self._holidays:
            return None
        t = local.time()
        for window in self._schedule.get(local.weekday(), ()):
            if window.start <= t < window.end:
                return window.session
        return None


def resolve_calendar(asset_class: AssetClass, exchange: str = "") -> TradingCalendar:
    """Select the calendar for an asset class (design doc sec 9).

    Crypto trades 24/7. Every other asset class (forex, metals, bonds, indices,
    ETFs, and exchange-traded futures) closes over the weekend, so it resolves
    to ``WeekendClosedCalendar`` until an ``exchange``-specific
    ``ExchangeCalendar`` is wired in for a known venue. The ``exchange``
    argument is that hook, unused until a real equity or futures adapter lands.
    """
    if asset_class == AssetClass.CRYPTO:
        return AlwaysOpenCalendar()
    return WeekendClosedCalendar()
