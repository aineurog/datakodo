"""Session aware resampling tests (design doc sec 8, 10, 18)."""

from datetime import date, time, timedelta

import numpy as np
import pandas as pd
import pytest

from datakodo.core.calendar import (
    FX_NY17,
    XNYS_REGULAR,
    ExchangeCalendar,
    SessionCalendar,
    SessionProfile,
    SessionWindow,
    WeekendClosedCalendar,
)
from datakodo.core.enums import Session, Timeframe
from datakodo.core.exceptions import DataValidationError
from datakodo.ops.resample import resample


def _frame(index: pd.DatetimeIndex, **extras) -> pd.DataFrame:
    count = len(index)
    data = {
        "timestamp": index,
        "open": np.full(count, 100.0),
        "high": np.full(count, 101.0),
        "low": np.full(count, 99.0),
        "close": np.full(count, 100.5),
        "volume": np.full(count, 100.0),
    }
    data.update(extras)
    return pd.DataFrame(data)


@pytest.fixture
def minute_df():
    """4 hours of 1-minute OHLCV data."""
    index = pd.date_range("2024-01-01 09:00", periods=240, freq="1min", tz="UTC")
    return _frame(index)


def _us_regular_calendar(holidays=(), early_closes=None):
    regular = SessionWindow(Session.REGULAR, time(9, 30), time(16, 0))
    schedule = {weekday: (regular,) for weekday in range(5)}
    return ExchangeCalendar(
        "America/New_York", schedule, holidays=holidays, early_closes=early_closes
    )


def _minute_index(windows) -> pd.DatetimeIndex:
    """One-minute timestamps covering each ``[start, end)`` window."""
    parts = [
        pd.date_range(
            pd.Timestamp(start).tz_convert("UTC"),
            pd.Timestamp(end).tz_convert("UTC"),
            freq="1min",
            inclusive="left",
        )
        for start, end in windows
    ]
    if not parts:
        return pd.DatetimeIndex([], tz="UTC")
    return parts[0].append(parts[1:])


def _xnys():
    """The exchange_calendars XNYS calendar, skipping when the extra is absent."""
    pytest.importorskip("exchange_calendars")
    from datakodo.core.exchange_calendars import get_library_calendar

    calendar = get_library_calendar("XNYS")
    assert calendar is not None
    return calendar


class TestResampleBasics:
    def test_upsample_1m_to_1h(self, minute_df):
        result = resample(minute_df, Timeframe.H1, source_timeframe=Timeframe.M1)
        assert len(result) == 4
        assert result["timestamp"].tolist() == list(
            pd.date_range("2024-01-01 09:00", periods=4, freq="1h", tz="UTC")
        )
        assert result["close_timestamp"].tolist() == list(
            pd.date_range("2024-01-01 10:00", periods=4, freq="1h", tz="UTC")
        )

    def test_resample_aggregates_correctly(self, minute_df):
        result = resample(minute_df, Timeframe.H1, source_timeframe=Timeframe.M1)
        assert result.loc[0, "open"] == minute_df.loc[0, "open"]
        assert result.loc[0, "high"] == minute_df.loc[:59, "high"].max()
        assert result.loc[0, "low"] == minute_df.loc[:59, "low"].min()
        assert result.loc[len(result) - 1, "close"] == minute_df.loc[239, "close"]

    def test_volume_is_summed(self, minute_df):
        result = resample(minute_df, Timeframe.H1, source_timeframe=Timeframe.M1)
        assert result.loc[0, "volume"] == 6000.0

    def test_reports_completeness_columns(self, minute_df):
        result = resample(minute_df, Timeframe.H1, source_timeframe=Timeframe.M1)
        assert (result["source_count"] == 60).all()
        assert (result["expected_count"] == 60).all()
        assert result["is_complete"].all()
        assert not result["is_partial"].any()
        assert result["is_closed"].all()

    def test_accepts_datetime_index(self, minute_df):
        result = resample(minute_df.set_index("timestamp"), Timeframe.H1, source_timeframe="1m")
        assert "timestamp" in result.columns

    def test_empty_df_raises(self):
        with pytest.raises(ValueError, match="empty"):
            resample(pd.DataFrame(), Timeframe.H1, source_timeframe=Timeframe.M1)

    def test_downsample_raises(self, minute_df):
        with pytest.raises(ValueError, match="upsampling"):
            resample(minute_df, Timeframe.M1, source_timeframe=Timeframe.M1)

        with pytest.raises(ValueError, match="upsampling"):
            resample(minute_df, Timeframe.M5, source_timeframe=Timeframe.H1)

    def test_source_timeframe_is_required(self, minute_df):
        with pytest.raises(TypeError):
            resample(minute_df, Timeframe.H1)


class TestSourceGrid:
    def test_missing_source_bar_marks_bucket_incomplete(self, minute_df):
        gappy = minute_df.drop(index=30)
        result = resample(gappy, Timeframe.H1, source_timeframe=Timeframe.M1)
        assert result.loc[0, "source_count"] == 59
        assert result.loc[0, "expected_count"] == 60
        assert not result.loc[0, "is_complete"]
        assert not result.loc[0, "is_closed"]
        assert result.loc[1, "is_complete"]

    def test_declared_source_survives_irregular_spacing(self):
        # Irregular source spacing must not be mistaken for a coarser timeframe.
        index = pd.DatetimeIndex(
            [
                "2024-01-01 09:00",
                "2024-01-01 09:07",
                "2024-01-01 09:08",
                "2024-01-01 09:30",
                "2024-01-01 09:59",
            ],
            tz="UTC",
        )
        result = resample(_frame(index), Timeframe.H1, source_timeframe=Timeframe.M1)
        assert result.loc[0, "source_count"] == 5
        assert result.loc[0, "expected_count"] == 60
        assert not result.loc[0, "is_complete"]


class TestIncompleteModes:
    @pytest.fixture
    def missing_bar(self, minute_df):
        return minute_df.drop(index=30)

    def test_keep_flags(self, missing_bar):
        result = resample(
            missing_bar, Timeframe.H1, source_timeframe=Timeframe.M1, incomplete="keep"
        )
        assert len(result) == 4
        assert not result.loc[0, "is_complete"]

    def test_drop_omits_and_warns(self, missing_bar, caplog):
        with caplog.at_level("WARNING"):
            result = resample(
                missing_bar, Timeframe.H1, source_timeframe=Timeframe.M1, incomplete="drop"
            )
        assert len(result) == 3
        assert result["is_closed"].all()
        assert "Dropped 1 resampled bar" in caplog.text

    def test_raise(self, missing_bar):
        with pytest.raises(DataValidationError, match="incomplete"):
            resample(missing_bar, Timeframe.H1, source_timeframe=Timeframe.M1, incomplete="raise")

    def test_invalid_mode_raises(self, minute_df):
        with pytest.raises(ValueError, match="Invalid incomplete mode"):
            resample(minute_df, Timeframe.H1, source_timeframe=Timeframe.M1, incomplete="nope")


class TestRequestBounds:
    def test_partial_first_bucket_is_flagged(self, minute_df):
        result = resample(
            minute_df,
            Timeframe.H1,
            source_timeframe=Timeframe.M1,
            start=pd.Timestamp("2024-01-01 09:30", tz="UTC"),
            end=pd.Timestamp("2024-01-01 13:00", tz="UTC"),
        )
        assert result.loc[0, "timestamp"] == pd.Timestamp("2024-01-01 09:00", tz="UTC")
        assert result.loc[0, "source_count"] == 30
        assert result.loc[0, "expected_count"] == 30
        assert result.loc[0, "is_complete"]
        assert result.loc[0, "is_partial"]
        assert not result.loc[0, "is_closed"]
        assert not result.loc[1, "is_partial"]

    def test_partial_last_bucket_is_flagged(self, minute_df):
        result = resample(
            minute_df,
            Timeframe.H1,
            source_timeframe=Timeframe.M1,
            end=pd.Timestamp("2024-01-01 11:30", tz="UTC"),
        )
        last = result.iloc[-1]
        assert last["timestamp"] == pd.Timestamp("2024-01-01 11:00", tz="UTC")
        assert last["source_count"] == 30
        assert last["expected_count"] == 30
        assert last["is_partial"]
        assert not last["is_closed"]

    def test_request_range_restricts_rows(self, minute_df):
        result = resample(
            minute_df,
            Timeframe.H1,
            source_timeframe=Timeframe.M1,
            start=pd.Timestamp("2024-01-01 10:00", tz="UTC"),
            end=pd.Timestamp("2024-01-01 11:00", tz="UTC"),
        )
        assert len(result) == 1
        assert result.loc[0, "timestamp"] == pd.Timestamp("2024-01-01 10:00", tz="UTC")

    def test_invalid_range_raises(self, minute_df):
        with pytest.raises(ValueError, match="must be before"):
            resample(
                minute_df,
                Timeframe.H1,
                source_timeframe=Timeframe.M1,
                start=pd.Timestamp("2024-01-01 11:00", tz="UTC"),
                end=pd.Timestamp("2024-01-01 10:00", tz="UTC"),
            )


class TestSessionProfile:
    def test_equity_daily_bar_covers_regular_session(self):
        profile = SessionProfile(
            timezone="America/New_York", open=time(9, 30), close=time(16), session=Session.REGULAR
        )
        index = pd.date_range(
            "2024-01-08 14:30", "2024-01-08 21:00", freq="1min", tz="UTC", inclusive="left"
        )
        result = resample(
            _frame(index),
            Timeframe.D1,
            source_timeframe=Timeframe.M1,
            session=profile,
            calendar=WeekendClosedCalendar(),
        )
        row = result.iloc[0]
        # 09:30 and 16:00 America/New_York in January (EST).
        assert row["timestamp"] == pd.Timestamp("2024-01-08 14:30", tz="UTC")
        assert row["close_timestamp"] == pd.Timestamp("2024-01-08 21:00", tz="UTC")
        assert row["session"] == "regular"
        assert row["source_count"] == 390
        assert row["expected_count"] == 390
        assert row["is_complete"] and row["is_closed"]

    def test_intraday_buckets_anchor_at_session_open(self):
        profile = SessionProfile(
            timezone="America/New_York", open=time(9, 30), close=time(16), session=Session.REGULAR
        )
        index = pd.date_range(
            "2024-01-08 14:30", "2024-01-08 21:00", freq="1min", tz="UTC", inclusive="left"
        )
        result = resample(
            _frame(index),
            Timeframe.H1,
            source_timeframe=Timeframe.M1,
            session=profile,
            calendar=WeekendClosedCalendar(),
        )
        assert result["timestamp"].tolist() == list(
            pd.date_range("2024-01-08 14:30", periods=7, freq="1h", tz="UTC")
        )
        assert result["source_count"].tolist() == [60, 60, 60, 60, 60, 60, 30]
        assert result["expected_count"].tolist() == [60, 60, 60, 60, 60, 60, 30]
        assert result["is_complete"].all()

    def test_fx_daily_bar_starts_at_rollover(self):
        profile = SessionProfile(
            timezone="America/New_York",
            open=time(17),
            close=time(17),
            starts_previous_day=True,
            session=Session.REGULAR,
        )
        index = pd.date_range(
            "2024-01-07 22:00", "2024-01-08 22:00", freq="1min", tz="UTC", inclusive="left"
        )
        result = resample(
            _frame(index),
            Timeframe.D1,
            source_timeframe=Timeframe.M1,
            session=profile,
            calendar=WeekendClosedCalendar(),
        )
        row = result.iloc[0]
        assert row["timestamp"] == pd.Timestamp("2024-01-07 22:00", tz="UTC")
        assert row["close_timestamp"] == pd.Timestamp("2024-01-08 22:00", tz="UTC")
        assert row["source_count"] == 1440
        assert row["is_complete"]

    def test_fx_rollover_follows_dst(self):
        profile = SessionProfile(
            timezone="America/New_York",
            open=time(17),
            close=time(17),
            starts_previous_day=True,
            session=Session.REGULAR,
        )
        # DST starts 2024-03-10: the Monday session opens Sunday 17:00 EDT (21:00 UTC).
        index = pd.date_range(
            "2024-03-10 21:00", "2024-03-11 21:00", freq="1min", tz="UTC", inclusive="left"
        )
        result = resample(
            _frame(index),
            Timeframe.D1,
            source_timeframe=Timeframe.M1,
            session=profile,
            calendar=WeekendClosedCalendar(),
        )
        assert result.loc[0, "timestamp"] == pd.Timestamp("2024-03-10 21:00", tz="UTC")
        assert result.loc[0, "close_timestamp"] == pd.Timestamp("2024-03-11 21:00", tz="UTC")
        assert result.loc[0, "is_complete"]

    def test_maintenance_break_splits_intraday_buckets(self):
        profile = SessionProfile(
            timezone="UTC",
            open=time(0),
            close=time(0),
            starts_previous_day=True,
            breaks=((time(5), time(6)),),
        )
        index = pd.date_range("2024-01-08", "2024-01-09", freq="1min", tz="UTC", inclusive="left")
        result = resample(
            _frame(index), Timeframe.H1, source_timeframe=Timeframe.M1, session=profile
        )
        assert pd.Timestamp("2024-01-08 05:00", tz="UTC") not in result["timestamp"].tolist()
        assert result.loc[4, "close_timestamp"] == pd.Timestamp("2024-01-08 05:00", tz="UTC")
        assert result.loc[5, "timestamp"] == pd.Timestamp("2024-01-08 06:00", tz="UTC")
        assert len(result) == 23

    def test_break_is_excluded_from_daily_expected_count(self):
        profile = SessionProfile(
            timezone="UTC",
            open=time(0),
            close=time(0),
            starts_previous_day=True,
            breaks=((time(5), time(6)),),
        )
        index = pd.date_range("2024-01-08", "2024-01-09", freq="1min", tz="UTC", inclusive="left")
        result = resample(
            _frame(index), Timeframe.D1, source_timeframe=Timeframe.M1, session=profile
        )
        assert result.loc[0, "source_count"] == 1380
        assert result.loc[0, "expected_count"] == 1380
        assert result.loc[0, "is_complete"]

    def test_no_session_column_without_label(self):
        profile = SessionProfile(timezone="UTC", open=time(9), close=time(17))
        index = pd.date_range(
            "2024-01-08 09:00", "2024-01-08 17:00", freq="1min", tz="UTC", inclusive="left"
        )
        result = resample(
            _frame(index), Timeframe.D1, source_timeframe=Timeframe.M1, session=profile
        )
        assert "session" not in result.columns

    def test_calendar_holiday_has_no_bucket(self):
        profile = SessionProfile(
            timezone="America/New_York", open=time(9, 30), close=time(16), session=Session.REGULAR
        )
        calendar = _us_regular_calendar(holidays=[date(2024, 1, 15)])  # MLK day
        index = pd.date_range(
            "2024-01-12 00:00", "2024-01-17 00:00", freq="1min", tz="UTC", inclusive="left"
        )
        result = resample(
            _frame(index),
            Timeframe.D1,
            source_timeframe=Timeframe.M1,
            session=profile,
            calendar=calendar,
        )
        assert result["timestamp"].dt.date.tolist() == [date(2024, 1, 12), date(2024, 1, 16)]
        assert (result["source_count"] == 390).all()

    def test_early_close_shortens_session(self):
        profile = SessionProfile(
            timezone="America/New_York", open=time(9, 30), close=time(16), session=Session.REGULAR
        )
        calendar = _us_regular_calendar(early_closes={date(2024, 1, 8): time(13)})
        index = pd.date_range(
            "2024-01-08 14:30", "2024-01-08 18:00", freq="1min", tz="UTC", inclusive="left"
        )
        result = resample(
            _frame(index),
            Timeframe.D1,
            source_timeframe=Timeframe.M1,
            session=profile,
            calendar=calendar,
        )
        row = result.iloc[0]
        assert row["close_timestamp"] == pd.Timestamp("2024-01-08 18:00", tz="UTC")
        assert row["source_count"] == 210
        assert row["expected_count"] == 210
        assert row["is_complete"]

    def test_rows_outside_session_return_empty_frame(self):
        profile = SessionProfile(
            timezone="America/New_York", open=time(9, 30), close=time(16), session=Session.REGULAR
        )
        index = pd.date_range("2024-01-08 00:00", periods=60, freq="1min", tz="UTC")
        result = resample(
            _frame(index), Timeframe.H1, source_timeframe=Timeframe.M1, session=profile
        )
        assert result.empty
        assert list(result.columns) == [
            "timestamp",
            "close_timestamp",
            "session",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "source_count",
            "expected_count",
            "is_complete",
            "is_partial",
            "is_closed",
        ]


class TestPeriodAnchors:
    def test_weekly_bar_starts_on_monday(self):
        index = pd.date_range("2024-01-01", "2024-01-15", freq="1min", tz="UTC", inclusive="left")
        result = resample(_frame(index), Timeframe.W1, source_timeframe=Timeframe.M1)
        assert result["timestamp"].tolist() == list(
            pd.date_range("2024-01-01", periods=2, freq="7D", tz="UTC")
        )
        assert result["close_timestamp"].tolist() == list(
            pd.date_range("2024-01-08", periods=2, freq="7D", tz="UTC")
        )
        assert (result["source_count"] == 10080).all()
        assert (result["expected_count"] == 10080).all()

    def test_monthly_bar_starts_on_first_session(self):
        index = pd.date_range("2024-01-01", "2024-02-01", freq="1min", tz="UTC", inclusive="left")
        result = resample(_frame(index), Timeframe.MN1, source_timeframe=Timeframe.M1)
        assert len(result) == 1
        assert result.loc[0, "timestamp"] == pd.Timestamp("2024-01-01", tz="UTC")
        assert result.loc[0, "close_timestamp"] == pd.Timestamp("2024-02-01", tz="UTC")
        assert result.loc[0, "source_count"] == 31 * 1440
        assert result.loc[0, "is_complete"]

    def test_weekly_anchor_skips_holiday(self):
        profile = SessionProfile(
            timezone="America/New_York", open=time(9, 30), close=time(16), session=Session.REGULAR
        )
        calendar = _us_regular_calendar(holidays=[date(2024, 1, 1)])
        index = pd.date_range("2024-01-01", "2024-01-06", freq="1min", tz="UTC", inclusive="left")
        result = resample(
            _frame(index),
            Timeframe.W1,
            source_timeframe=Timeframe.M1,
            session=profile,
            calendar=calendar,
        )
        row = result.iloc[0]
        # First trading day of the week is Tuesday 2024-01-02 09:30 ET.
        assert row["timestamp"] == pd.Timestamp("2024-01-02 14:30", tz="UTC")
        assert row["close_timestamp"] == pd.Timestamp("2024-01-05 21:00", tz="UTC")
        assert row["source_count"] == 4 * 390
        assert row["expected_count"] == 4 * 390
        assert row["is_complete"]

    def test_daily_source_to_weekly(self):
        profile = SessionProfile(
            timezone="UTC", open=time(9, 30), close=time(16), session=Session.REGULAR
        )
        index = pd.date_range("2024-01-01 09:30", periods=5, freq="1D", tz="UTC")
        result = resample(
            _frame(index), Timeframe.W1, source_timeframe=Timeframe.D1, session=profile
        )
        assert result.loc[0, "source_count"] == 5
        assert result.loc[0, "expected_count"] == 5
        assert result.loc[0, "is_complete"]
        assert not result.loc[0, "is_partial"]

    def test_weekly_source_to_monthly(self):
        index = pd.date_range("2024-01-01", periods=5, freq="7D", tz="UTC")
        result = resample(_frame(index), Timeframe.MN1, source_timeframe=Timeframe.W1)
        assert result.loc[0, "source_count"] == 5
        assert result.loc[0, "expected_count"] == 5
        assert result.loc[0, "is_complete"]
        assert not result.loc[0, "is_partial"]


class TestOptionalColumns:
    def test_additive_columns_sum_and_spread_takes_max(self):
        index = pd.date_range("2024-01-01 09:00", periods=120, freq="1min", tz="UTC")
        spread = np.where(np.arange(120) % 2 == 0, 0.01, 0.03)
        frame = _frame(
            index,
            volume=np.ones(120),
            quote_volume=np.full(120, 2.0),
            trades_count=np.full(120, 3),
            taker_buy_base_volume=np.full(120, 0.4),
            taker_buy_quote_volume=np.full(120, 0.8),
            turnover=np.full(120, 5.0),
            real_volume=np.full(120, 6.0),
            vwap=np.arange(120, dtype=float),
            spread=spread,
            not_a_column=9,
        )
        result = resample(frame, Timeframe.H1, source_timeframe=Timeframe.M1)
        first = result.iloc[0]
        assert first["quote_volume"] == 120.0
        assert first["trades_count"] == 180
        assert first["taker_buy_base_volume"] == pytest.approx(24.0)
        assert first["taker_buy_quote_volume"] == pytest.approx(48.0)
        assert first["turnover"] == 300.0
        assert first["real_volume"] == 360.0
        assert first["spread"] == 0.03
        assert first["vwap"] == pytest.approx(np.arange(60).mean())
        assert "not_a_column" not in result.columns

    def test_vwap_is_none_when_source_vwap_is_missing(self):
        index = pd.date_range("2024-01-01 09:00", periods=60, freq="1min", tz="UTC")
        vwap = np.full(60, 100.0)
        vwap[10] = np.nan
        result = resample(_frame(index, vwap=vwap), Timeframe.H1, source_timeframe=Timeframe.M1)
        assert pd.isna(result.loc[0, "vwap"])

    def test_absent_optional_columns_are_not_invented(self, minute_df):
        result = resample(minute_df, Timeframe.H1, source_timeframe=Timeframe.M1)
        assert "quote_volume" not in result.columns
        assert "vwap" not in result.columns


class TestInputValidation:
    def test_naive_timestamps_raise(self):
        index = pd.date_range("2024-01-01 09:00", periods=10, freq="1min")
        with pytest.raises(ValueError, match="timezone aware"):
            resample(_frame(index), Timeframe.H1, source_timeframe=Timeframe.M1)

    def test_duplicate_timestamps_raise(self):
        index = pd.DatetimeIndex(["2024-01-01 09:00", "2024-01-01 09:00"], tz="UTC")
        with pytest.raises(ValueError, match="Duplicate"):
            resample(_frame(index), Timeframe.H1, source_timeframe=Timeframe.M1)

    def test_missing_columns_raise(self):
        frame = pd.DataFrame(
            {
                "timestamp": pd.date_range("2024-01-01", periods=2, freq="1min", tz="UTC"),
                "open": [1.0, 1.0],
            }
        )
        with pytest.raises(ValueError, match="missing required"):
            resample(frame, Timeframe.H1, source_timeframe=Timeframe.M1)

    def test_missing_timestamp_raises(self):
        frame = pd.DataFrame(
            {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]}
        )
        with pytest.raises(ValueError, match="DatetimeIndex"):
            resample(frame, Timeframe.H1, source_timeframe=Timeframe.M1)


class TestCalendarFiltering:
    def test_drops_non_trading_day_bars(self):
        index = pd.date_range("2024-01-05", "2024-01-09", freq="1h", tz="UTC", inclusive="left")
        result = resample(
            _frame(index),
            Timeframe.D1,
            source_timeframe=Timeframe.H1,
            calendar=WeekendClosedCalendar(),
        )
        assert result["timestamp"].dt.date.tolist() == [date(2024, 1, 5), date(2024, 1, 8)]

    def test_without_calendar_keeps_weekend_bars(self):
        index = pd.date_range("2024-01-05", "2024-01-09", freq="1h", tz="UTC", inclusive="left")
        result = resample(_frame(index), Timeframe.D1, source_timeframe=Timeframe.H1)
        assert len(result) == 4


class TestAcceptanceSessions:
    """The Phase 1b/2 session and aggregation acceptance cases."""

    def test_fx_daily_bars_are_complete_and_anchor_at_ny_rollover(self):
        calendar = SessionCalendar(FX_NY17)
        start = pd.Timestamp("2024-01-07 17:00", tz="America/New_York").tz_convert("UTC")
        end = pd.Timestamp("2024-01-12 17:00", tz="America/New_York").tz_convert("UTC")
        index = _minute_index(calendar.session_windows_between(start, end))
        frame = _frame(
            index,
            quote_volume=np.ones(len(index)),
            trades_count=np.ones(len(index)),
        )
        result = resample(
            frame,
            Timeframe.D1,
            source_timeframe=Timeframe.M1,
            session=FX_NY17,
            calendar=calendar,
        )
        # Monday through Friday, each timestamped at the 17:00 New York rollover.
        assert result["timestamp"].tolist() == [
            pd.Timestamp(f"2024-01-{day:02d} 22:00", tz="UTC") for day in range(7, 12)
        ]
        local = result["timestamp"].dt.tz_convert("America/New_York")
        assert (local.dt.hour == 17).all() and (local.dt.minute == 0).all()
        # 24h nominal window minus the 1h maintenance break.
        assert (result["source_count"] == 1380).all()
        assert (result["expected_count"] == 1380).all()
        assert result["is_complete"].all() and result["is_closed"].all()
        # Optional columns survive the daily aggregation.
        assert (result["quote_volume"] == 1380).all()
        assert (result["trades_count"] == 1380).all()

    @pytest.mark.parametrize(
        ("session_start", "duration", "source_minutes"),
        [
            ("2024-03-09 22:00", timedelta(hours=23), 23 * 60),
            ("2024-11-02 21:00", timedelta(hours=25), 25 * 60),
        ],
    )
    def test_dst_weekend_daily_bar_is_23_or_25_hours(self, session_start, duration, source_minutes):
        profile = SessionProfile(
            timezone="America/New_York",
            open=time(17),
            close=time(17),
            starts_previous_day=True,
            weekdays=frozenset(range(7)),
        )
        calendar = SessionCalendar(profile)
        start = pd.Timestamp(session_start, tz="UTC")
        end = start + duration
        index = _minute_index(calendar.session_windows_between(start, end))
        result = resample(
            _frame(index),
            Timeframe.D1,
            source_timeframe=Timeframe.M1,
            session=profile,
            calendar=calendar,
        )
        assert len(result) == 1
        row = result.iloc[0]
        assert row["timestamp"] == start
        assert row["close_timestamp"] == end
        assert row["close_timestamp"] - row["timestamp"] == duration
        assert row["source_count"] == source_minutes
        assert row["expected_count"] == source_minutes
        assert row["is_complete"] and row["is_closed"]

    def test_dst_moves_the_daily_boundary_by_one_utc_hour(self):
        spring_close = pd.Timestamp("2024-03-10 21:00", tz="UTC")
        fall_close = pd.Timestamp("2024-11-03 22:00", tz="UTC")
        # Both are 17:00 New York, but their UTC hour differs after the change.
        assert spring_close.tz_convert("America/New_York").hour == 17
        assert fall_close.tz_convert("America/New_York").hour == 17
        assert abs(spring_close.hour - fall_close.hour) == 1

    def test_missing_minute_is_dropped_and_reported(self, minute_df):
        gappy = minute_df.drop(index=30)
        result = resample(gappy, Timeframe.H1, source_timeframe=Timeframe.M1, incomplete="drop")
        assert len(result) == 3
        diagnostics = result.attrs["resample_diagnostics"]
        assert diagnostics["dropped_count"] == 1
        assert diagnostics["dropped_ranges"] == [
            (
                pd.Timestamp("2024-01-01 09:00", tz="UTC"),
                pd.Timestamp("2024-01-01 10:00", tz="UTC"),
            )
        ]

    def test_backfill_starting_mid_hour_has_no_partial_bar(self):
        index = pd.date_range("2024-01-01 00:30", periods=120, freq="1min", tz="UTC")
        result = resample(
            _frame(index),
            Timeframe.H1,
            source_timeframe=Timeframe.M1,
            start=pd.Timestamp("2024-01-01 00:30", tz="UTC"),
            end=pd.Timestamp("2024-01-01 02:30", tz="UTC"),
            incomplete="drop",
        )
        assert pd.Timestamp("2024-01-01 00:00", tz="UTC") not in result["timestamp"].tolist()
        assert result["timestamp"].tolist() == [pd.Timestamp("2024-01-01 01:00", tz="UTC")]

    def test_monthly_bar_starts_at_first_session_open(self):
        calendar = _xnys()
        index = _minute_index(
            calendar.session_windows_between(
                pd.Timestamp("2024-02-01 00:00", tz="UTC"),
                pd.Timestamp("2024-03-01 00:00", tz="UTC"),
            )
        )
        result = resample(
            _frame(index),
            Timeframe.MN1,
            source_timeframe=Timeframe.M1,
            session=XNYS_REGULAR,
            calendar=calendar,
        )
        assert len(result) == 1
        assert result.loc[0, "timestamp"] == pd.Timestamp("2024-02-01 14:30", tz="UTC")
        assert result.loc[0, "close_timestamp"] == pd.Timestamp("2024-02-29 21:00", tz="UTC")
        assert result.loc[0, "is_complete"]

    def test_weekly_bar_starts_at_first_session_open(self):
        calendar = _xnys()
        index = _minute_index(
            calendar.session_windows_between(
                pd.Timestamp("2024-01-08 00:00", tz="UTC"),
                pd.Timestamp("2024-01-13 00:00", tz="UTC"),
            )
        )
        result = resample(
            _frame(index),
            Timeframe.W1,
            source_timeframe=Timeframe.M1,
            session=XNYS_REGULAR,
            calendar=calendar,
        )
        assert len(result) == 1
        assert result.loc[0, "timestamp"] == pd.Timestamp("2024-01-08 14:30", tz="UTC")
        assert result.loc[0, "close_timestamp"] == pd.Timestamp("2024-01-12 21:00", tz="UTC")
        assert result.loc[0, "is_complete"]

    def test_xnys_half_day_closes_daily_and_4h_bars_early(self):
        calendar = _xnys()
        index = _minute_index(
            calendar.session_windows_between(
                pd.Timestamp("2024-11-27 00:00", tz="UTC"),
                pd.Timestamp("2024-11-30 00:00", tz="UTC"),
            )
        )
        frame = _frame(index)
        daily = resample(
            frame,
            Timeframe.D1,
            source_timeframe=Timeframe.M1,
            session=XNYS_REGULAR,
            calendar=calendar,
        )
        assert daily["timestamp"].dt.date.tolist() == [date(2024, 11, 27), date(2024, 11, 29)]
        assert daily["close_timestamp"].tolist() == [
            pd.Timestamp("2024-11-27 21:00", tz="UTC"),
            pd.Timestamp("2024-11-29 18:00", tz="UTC"),
        ]
        assert daily["source_count"].tolist() == [390, 210]
        assert daily["is_complete"].all()

        h4 = resample(
            frame,
            Timeframe.H4,
            source_timeframe=Timeframe.M1,
            session=XNYS_REGULAR,
            calendar=calendar,
        )
        last = h4.iloc[-1]
        assert last["timestamp"] == pd.Timestamp("2024-11-29 14:30", tz="UTC")
        assert last["close_timestamp"] == pd.Timestamp("2024-11-29 18:00", tz="UTC")
        assert last["is_complete"]
