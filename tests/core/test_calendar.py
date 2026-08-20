"""Trading calendar tests (design doc sec 9)."""

from datetime import UTC, date, datetime, time

from datakodo.core.calendar import (
    AlwaysOpenCalendar,
    ExchangeCalendar,
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


class TestResolveCalendar:
    def test_crypto_is_always_open(self):
        assert isinstance(resolve_calendar(AssetClass.CRYPTO), AlwaysOpenCalendar)

    def test_non_crypto_is_weekend_closed(self):
        for asset_class in (
            AssetClass.FOREX,
            AssetClass.METAL,
            AssetClass.EQUITY,
            AssetClass.BOND,
            AssetClass.INDEX,
            AssetClass.ETF,
        ):
            assert isinstance(resolve_calendar(asset_class), WeekendClosedCalendar)
