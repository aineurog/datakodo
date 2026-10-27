"""Optional ``exchange_calendars`` backed venue calendars.

The core calendar interface is the source of truth. This module only adapts a
maintained exchange calendar (real holidays, half days, and DST-aware session
opens and closes) into a :class:`~datakodo.core.calendar.TradingCalendar`.
``exchange_calendars`` is an optional extra (``datakodo[calendar]``); when it
is not installed every lookup returns ``None`` and callers fall back to the
built-in calendars.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import cast

import pandas as pd

from datakodo.core.calendar import TradingCalendar, _as_utc
from datakodo.core.enums import Session


def get_library_calendar(
    name: str,
    *,
    session: Session | None = Session.REGULAR,
) -> LibraryCalendar | None:
    """Return an ``exchange_calendars`` calendar under *name*, or ``None``.

    ``name`` is a MIC or a common venue alias (``NYSE``, ``NASDAQ``, ``CME``).
    An unknown venue or a missing optional dependency returns ``None`` so the
    caller can fall back without a hard failure. ``session`` is the label
    ``session_for`` reports; pass ``None`` for venues that have no canonical
    single session (futures).
    """
    alias = {
        "NYSE": "XNYS",
        "NASDAQ": "XNAS",
        "ARCA": "ARCX",
        "NYSEARCA": "ARCX",
        "CME": "CMES",
        "GLOBEX": "CMES",
        "CBOT": "XCBT",
        "COMEX": "XCEC",
        "NYMEX": "XNYM",
    }
    try:
        import exchange_calendars as xc
    except ImportError:
        return None
    key = (name or "").strip().upper()
    key = alias.get(key, key)
    if not key:
        return None
    try:
        calendar = xc.get_calendar(key)
    except (ValueError, KeyError):
        return None
    return LibraryCalendar(calendar, name=name, session=session)


class LibraryCalendar(TradingCalendar):
    """A thin adapter over an ``exchange_calendars`` instance."""

    def __init__(self, calendar, name: str = "", session: Session | None = Session.REGULAR) -> None:
        self._calendar = calendar
        self.name = name
        self._session = session

    def is_trading_day(self, day: date | datetime) -> bool:
        try:
            return bool(self._calendar.is_session(pd.Timestamp(day)))
        except (TypeError, ValueError):
            return False

    def session_for(self, timestamp: datetime) -> Session | None:
        ts = pd.Timestamp(_as_utc(timestamp))
        try:
            if not self._calendar.is_open_on_minute(ts):
                return None
        except (TypeError, ValueError):
            return None
        return self._session

    def early_close(self, day: date | datetime) -> time | None:
        session = pd.Timestamp(day)
        try:
            if not self._calendar.is_session(session):
                return None
            if session not in self._calendar.early_closes:
                return None
            close = self._calendar.session_close(session).tz_convert(self._calendar.tz)
        except (KeyError, TypeError, ValueError):
            return None
        return cast(time, close.time())

    def session_windows_between(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        start_ts = pd.Timestamp(_as_utc(start)).tz_convert("UTC")
        end_ts = pd.Timestamp(_as_utc(end)).tz_convert("UTC")
        # exchange_calendars takes timezone-naive session dates. Expand by a
        # day on each side so the exchange-local date cannot fall outside the
        # range; the per-session clip below removes the extra sessions.
        tz = self._calendar.tz
        first = (start_ts.tz_convert(tz) - pd.Timedelta(days=1)).tz_localize(None).normalize()
        last = (end_ts.tz_convert(tz) + pd.Timedelta(days=1)).tz_localize(None).normalize()
        try:
            sessions = self._calendar.sessions_in_range(first, last)
        except (TypeError, ValueError):
            return []
        windows: list[tuple[datetime, datetime]] = []
        for session in sessions:
            session_open = self._calendar.session_open(session)
            session_close = self._calendar.session_close(session)
            lower = max(session_open, start_ts)
            upper = min(session_close, end_ts)
            if lower < upper:
                windows.append((lower.to_pydatetime(), upper.to_pydatetime()))
        return windows

    def trading_date_for(self, timestamp: datetime) -> date | None:
        ts = pd.Timestamp(_as_utc(timestamp))
        try:
            if not self._calendar.is_open_on_minute(ts):
                return None
            return cast(date, self._calendar.minute_to_session(ts).date())
        except (TypeError, ValueError):
            return None
