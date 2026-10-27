"""Trading calendar tests (design doc sec 9)."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from datakodo.core.calendar import (
    AlwaysOpenCalendar,
    ExchangeCalendar,
    SessionCalendar,
    SessionProfile,
    SessionWindow,
    WeekendClosedCalendar,
    resolve_calendar,
)
from datakodo.core.enums import AssetClass, Session


def _us_equity_calendar(holidays=()):
    """A New York exchange calendar with pre/regular/post sessions, Mon-Fri."""
    pre = SessionWindow(Session.PRE_MARKET, time(4, 0), time(9, 30))
    regular = SessionWindow(Session.REGULAR, time(9, 30), time(16, 0))
    post = SessionWindow(Session.POST_MARKET, time(16, 0), time(20, 0))
    schedule = {weekday: (pre, regular, post) for weekday in range(5)}
    return ExchangeCalendar("America/New_York", schedule, holidays=holidays)


class TestAlwaysOpenCalendar:
    def test_every_day_trades(self):
        cal = AlwaysOpenCalendar()
        assert cal.is_trading_day(date(2026, 1, 10))  # Saturday
        assert cal.is_trading_day(date(2026, 1, 11))  # Sunday
        assert cal.is_trading_day(datetime(2026, 1, 5, tzinfo=UTC))

    def test_no_session(self):
        assert AlwaysOpenCalendar().session_for(datetime(2026, 1, 5, tzinfo=UTC)) is None


class TestWeekendClosedCalendar:
    def test_weekdays_trade_weekends_close(self):
        cal = WeekendClosedCalendar()
        assert cal.is_trading_day(date(2026, 1, 5))  # Monday
        assert cal.is_trading_day(date(2026, 1, 9))  # Friday
        assert not cal.is_trading_day(date(2026, 1, 10))  # Saturday
        assert not cal.is_trading_day(date(2026, 1, 11))  # Sunday

    def test_accepts_datetime(self):
        cal = WeekendClosedCalendar()
        assert cal.is_trading_day(datetime(2026, 1, 9, tzinfo=UTC))
        assert not cal.is_trading_day(datetime(2026, 1, 10, tzinfo=UTC))

    def test_no_session(self):
        assert WeekendClosedCalendar().session_for(datetime(2026, 1, 5, tzinfo=UTC)) is None

    def test_pure_weekend_has_no_trading_day_between(self):
        cal = WeekendClosedCalendar()
        # Friday 2026-01-09 -> Monday 2026-01-12: only Sat/Sun strictly between.
        assert not cal.has_trading_day_between(
            datetime(2026, 1, 9, tzinfo=UTC), datetime(2026, 1, 12, tzinfo=UTC)
        )

    def test_weekday_between_is_detected(self):
        cal = WeekendClosedCalendar()
        # Friday -> Tuesday: Monday is a trading day strictly between.
        assert cal.has_trading_day_between(
            datetime(2026, 1, 9, tzinfo=UTC), datetime(2026, 1, 13, tzinfo=UTC)
        )


class TestExchangeCalendar:
    def test_is_trading_day_respects_schedule_and_holidays(self):
        cal = _us_equity_calendar(holidays=[date(2026, 1, 1)])
        assert cal.is_trading_day(date(2026, 1, 5))  # Monday
        assert not cal.is_trading_day(date(2026, 1, 10))  # Saturday
        assert not cal.is_trading_day(date(2026, 1, 1))  # New Year holiday (Thursday)

    def test_session_for_converts_utc_to_local(self):
        cal = _us_equity_calendar()
        # 2026-01-05 15:00 UTC == 10:00 America/New_York (EST) -> regular.
        assert cal.session_for(datetime(2026, 1, 5, 15, 0, tzinfo=UTC)) is Session.REGULAR

    def test_session_for_pre_market(self):
        cal = _us_equity_calendar()
        # 09:00 UTC == 04:00 ET -> pre_market.
        assert cal.session_for(datetime(2026, 1, 5, 9, 0, tzinfo=UTC)) is Session.PRE_MARKET

    def test_session_for_post_market(self):
        cal = _us_equity_calendar()
        # 21:00 UTC == 16:00 ET -> post_market.
        assert cal.session_for(datetime(2026, 1, 5, 21, 0, tzinfo=UTC)) is Session.POST_MARKET

    def test_session_for_outside_windows_is_none(self):
        cal = _us_equity_calendar()
        # 08:00 UTC == 03:00 ET -> before pre_market.
        assert cal.session_for(datetime(2026, 1, 5, 8, 0, tzinfo=UTC)) is None

    def test_session_for_weekend_is_none(self):
        cal = _us_equity_calendar()
        # 2026-01-10 is a Saturday.
        assert cal.session_for(datetime(2026, 1, 10, 15, 0, tzinfo=UTC)) is None

    def test_session_for_holiday_is_none(self):
        cal = _us_equity_calendar(holidays=[date(2026, 1, 1)])
        assert cal.session_for(datetime(2026, 1, 1, 15, 0, tzinfo=UTC)) is None

    def test_session_for_naive_timestamp_assumed_utc(self):
        cal = _us_equity_calendar()
        assert cal.session_for(datetime(2026, 1, 5, 15, 0)) is Session.REGULAR

    def test_session_for_boundary_inclusive_start(self):
        cal = _us_equity_calendar()
        # 14:30 UTC == 09:30 ET is the first regular minute.
        assert cal.session_for(datetime(2026, 1, 5, 14, 30, tzinfo=UTC)) is Session.REGULAR

    def test_has_trading_day_between_skips_holiday(self):
        # Wednesday 2025-12-31 -> Monday 2026-01-05: Thursday (holiday) and
        # Friday (trading) lie strictly between, so a trading day exists.
        cal = _us_equity_calendar(holidays=[date(2026, 1, 1)])
        assert cal.has_trading_day_between(
            datetime(2025, 12, 31, tzinfo=UTC), datetime(2026, 1, 5, tzinfo=UTC)
        )


class TestSessionProfile:
    def test_same_day_window(self):
        profile = SessionProfile(timezone="UTC", open=time(9), close=time(17))
        windows = profile.windows(date(2024, 1, 8))
        assert windows == [
            (
                datetime(2024, 1, 8, 9, 0, tzinfo=UTC),
                datetime(2024, 1, 8, 17, 0, tzinfo=UTC),
            )
        ]

    def test_previous_day_window_converts_timezone(self):
        profile = SessionProfile(
            timezone="America/New_York",
            open=time(17),
            close=time(17),
            starts_previous_day=True,
        )
        # January is EST (UTC-5): Sunday 17:00 -> Monday 17:00.
        assert profile.windows(date(2024, 1, 8)) == [
            (
                datetime(2024, 1, 7, 22, 0, tzinfo=UTC),
                datetime(2024, 1, 8, 22, 0, tzinfo=UTC),
            )
        ]

    def test_previous_day_window_follows_dst(self):
        profile = SessionProfile(
            timezone="America/New_York",
            open=time(17),
            close=time(17),
            starts_previous_day=True,
        )
        # DST started on 2024-03-10, so the session is an hour earlier in UTC.
        assert profile.windows(date(2024, 3, 11)) == [
            (
                datetime(2024, 3, 10, 21, 0, tzinfo=UTC),
                datetime(2024, 3, 11, 21, 0, tzinfo=UTC),
            )
        ]

    def test_early_close_shortens_window(self):
        profile = SessionProfile(timezone="America/New_York", open=time(9, 30), close=time(16))
        assert profile.windows(date(2024, 1, 8), early_close=time(13)) == [
            (
                datetime(2024, 1, 8, 14, 30, tzinfo=UTC),
                datetime(2024, 1, 8, 18, 0, tzinfo=UTC),
            )
        ]

    def test_early_close_cannot_extend_window(self):
        profile = SessionProfile(timezone="UTC", open=time(9), close=time(17))
        assert profile.windows(date(2024, 1, 8), early_close=time(20)) == [
            (
                datetime(2024, 1, 8, 9, 0, tzinfo=UTC),
                datetime(2024, 1, 8, 17, 0, tzinfo=UTC),
            )
        ]

    def test_break_splits_window(self):
        profile = SessionProfile(
            timezone="UTC",
            open=time(0),
            close=time(0),
            starts_previous_day=True,
            breaks=((time(5), time(6)),),
        )
        assert profile.windows(date(2024, 1, 8)) == [
            (
                datetime(2024, 1, 7, 0, 0, tzinfo=UTC),
                datetime(2024, 1, 7, 5, 0, tzinfo=UTC),
            ),
            (
                datetime(2024, 1, 7, 6, 0, tzinfo=UTC),
                datetime(2024, 1, 8, 0, 0, tzinfo=UTC),
            ),
        ]

    def test_weekdays_are_normalized(self):
        profile = SessionProfile(timezone="UTC", open=time(9), close=time(17), weekdays={0, 2, 4})
        assert profile.weekdays == frozenset({0, 2, 4})

    def test_session_label_is_normalized(self):
        profile = SessionProfile(timezone="UTC", open=time(9), close=time(17), session="regular")
        assert profile.session is Session.REGULAR

    def test_unknown_timezone_raises(self):
        with pytest.raises(ValueError, match="Unknown timezone"):
            SessionProfile(timezone="Mars/Olympus", open=time(9), close=time(17))

    def test_empty_weekdays_raises(self):
        with pytest.raises(ValueError, match="weekdays"):
            SessionProfile(timezone="UTC", open=time(9), close=time(17), weekdays=set())

    def test_invalid_weekday_raises(self):
        with pytest.raises(ValueError, match="weekdays"):
            SessionProfile(timezone="UTC", open=time(9), close=time(17), weekdays={7})

    def test_same_day_requires_open_before_close(self):
        with pytest.raises(ValueError, match="open < close"):
            SessionProfile(timezone="UTC", open=time(17), close=time(9))

    def test_previous_day_rejects_close_after_open(self):
        with pytest.raises(ValueError, match="close <= open"):
            SessionProfile(
                timezone="UTC",
                open=time(9),
                close=time(17),
                starts_previous_day=True,
            )

    def test_break_must_be_ordered(self):
        with pytest.raises(ValueError, match="breaks"):
            SessionProfile(
                timezone="UTC",
                open=time(9),
                close=time(17),
                breaks=((time(12), time(11)),),
            )


class TestEarlyClose:
    def test_default_calendars_have_no_early_close(self):
        assert AlwaysOpenCalendar().early_close(date(2024, 1, 8)) is None
        assert WeekendClosedCalendar().early_close(date(2024, 1, 8)) is None

    def test_exchange_calendar_override(self):
        calendar = ExchangeCalendar(
            "America/New_York",
            {
                weekday: (SessionWindow(Session.REGULAR, time(9, 30), time(16)),)
                for weekday in range(5)
            },
            early_closes={date(2024, 11, 29): time(13)},
        )
        assert calendar.early_close(date(2024, 11, 29)) == time(13)
        assert calendar.early_close(datetime(2024, 11, 29, 15, tzinfo=UTC)) == time(13)
        assert calendar.early_close(date(2024, 11, 28)) is None

    def test_half_day_shortens_windows_and_expected_bars(self):
        calendar = ExchangeCalendar(
            "America/New_York",
            {
                weekday: (SessionWindow(Session.REGULAR, time(9, 30), time(16)),)
                for weekday in range(5)
            },
            early_closes={date(2024, 11, 29): time(13)},
        )
        # 09:30-13:00 Eastern is 14:30-18:00 UTC (EST, UTC-5).
        windows = calendar.session_windows_between(
            datetime(2024, 11, 29, tzinfo=UTC),
            datetime(2024, 11, 30, tzinfo=UTC),
        )
        assert windows == [
            (
                datetime(2024, 11, 29, 14, 30, tzinfo=UTC),
                datetime(2024, 11, 29, 18, 0, tzinfo=UTC),
            )
        ]
        assert (
            calendar.expected_bars(
                datetime(2024, 11, 29, tzinfo=UTC),
                datetime(2024, 11, 30, tzinfo=UTC),
                timedelta(minutes=15),
            )
            == 14
        )


class TestResolveCalendar:
    def test_crypto_is_always_open(self):
        assert isinstance(resolve_calendar(AssetClass.CRYPTO), AlwaysOpenCalendar)

    def test_forex_and_metals_use_new_york_rollover(self):
        assert isinstance(resolve_calendar(AssetClass.FOREX), SessionCalendar)
        assert isinstance(resolve_calendar(AssetClass.METAL), SessionCalendar)

    def test_other_asset_classes_fall_back_to_weekend_closed(self):
        for asset_class in (
            AssetClass.EQUITY,
            AssetClass.BOND,
            AssetClass.INDEX,
            AssetClass.ETF,
        ):
            assert isinstance(resolve_calendar(asset_class), WeekendClosedCalendar)
