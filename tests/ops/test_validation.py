"""Data quality validation tests."""

from datetime import date, time

import pandas as pd
import pytest

from datakodo.core.calendar import (
    FX_NY17,
    XNYS_REGULAR,
    ExchangeCalendar,
    SessionCalendar,
    SessionWindow,
    WeekendClosedCalendar,
)
from datakodo.core.enums import Session
from datakodo.core.exceptions import DataValidationError
from datakodo.ops.validation import (
    anchor_period_bars,
    apply_fetch_contract,
    bar_close_times,
    detect_gaps,
    validate_ohlcv,
)


class TestValidateOHLCV:
    def test_valid_dataframe_passes(self, sample_ohlcv_df):
        validate_ohlcv(sample_ohlcv_df)

    def test_empty_dataframe_raises(self):
        df = pd.DataFrame()
        with pytest.raises(DataValidationError, match="empty"):
            validate_ohlcv(df)

    def test_missing_columns_raises(self):
        df = pd.DataFrame({"wrong_col": [1, 2, 3]})
        with pytest.raises(DataValidationError, match="Missing"):
            validate_ohlcv(df)

    def test_negative_prices_raise(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.loc[2, "open"] = -1.0
        with pytest.raises(DataValidationError, match="negative"):
            validate_ohlcv(df)

    def test_negative_volume_raises(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.loc[1, "volume"] = -100.0
        with pytest.raises(DataValidationError, match="negative"):
            validate_ohlcv(df)

    def test_high_below_low_raises(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.loc[3, "high"] = 50.0
        df.loc[3, "low"] = 100.0
        with pytest.raises(DataValidationError, match="high < low"):
            validate_ohlcv(df)

    def test_non_monotonic_timestamps_raise(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        timestamps = df["timestamp"].tolist()
        timestamps[2], timestamps[3] = timestamps[3], timestamps[2]
        df["timestamp"] = timestamps
        with pytest.raises(DataValidationError, match="monotonically"):
            validate_ohlcv(df)

    def test_duplicate_timestamps_raise(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        # Make timestamps monotonically increasing, then create a duplicate.
        df = df.sort_values("timestamp")
        df.loc[4, "timestamp"] = df.loc[0, "timestamp"]
        df = df.sort_values("timestamp")
        with pytest.raises(DataValidationError, match="Duplicate"):
            validate_ohlcv(df)

    def test_nan_raises_with_offending_range(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.loc[2, "close"] = float("nan")
        with pytest.raises(DataValidationError, match="NaN or infinite") as exc_info:
            validate_ohlcv(df)
        err = exc_info.value
        assert err.count == 1
        assert err.start == df.loc[2, "timestamp"].to_pydatetime()
        assert err.end == err.start

    def test_infinite_value_raises(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.loc[1, "high"] = float("inf")
        with pytest.raises(DataValidationError, match="NaN or infinite"):
            validate_ohlcv(df)

    def test_negative_price_error_carries_range(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.loc[3, "low"] = -5.0
        with pytest.raises(DataValidationError) as exc_info:
            validate_ohlcv(df)
        err = exc_info.value
        assert err.count == 1
        assert err.start == df.loc[3, "timestamp"].to_pydatetime()

    def test_negative_prices_allowed_when_instrument_opts_in(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.loc[3, "low"] = -5.0
        validate_ohlcv(df, allow_negative_prices=True)

    def test_negative_volume_rejected_even_when_prices_opted_in(self, sample_ohlcv_df):
        df = sample_ohlcv_df.copy()
        df.loc[1, "volume"] = -100.0
        with pytest.raises(DataValidationError, match="volume"):
            validate_ohlcv(df, allow_negative_prices=True)


def _ohlcv(timestamps) -> pd.DataFrame:
    """A minimal OHLCV frame with the given tz-aware timestamps."""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(timestamps, utc=True),
            "open": [1.0] * len(timestamps),
            "high": [1.1] * len(timestamps),
            "low": [0.9] * len(timestamps),
            "close": [1.05] * len(timestamps),
            "volume": [100.0] * len(timestamps),
        }
    )


class TestDetectGapsCalendarAware:
    def test_weekend_gap_suppressed(self):
        """1h bars from the Friday close to the Sunday open expect no bars."""
        df = _ohlcv(["2026-01-09 22:00", "2026-01-12 00:00"])
        assert not detect_gaps(df, "1h").empty  # flagged without a calendar
        assert detect_gaps(df, "1h", calendar=SessionCalendar(FX_NY17)).empty

    def test_same_day_gap_always_flagged(self):
        """A hole within one day is real even with a calendar."""
        df = _ohlcv(["2026-01-12 09:00", "2026-01-12 12:00"])
        gaps = detect_gaps(df, "1h", calendar=WeekendClosedCalendar())
        assert len(gaps) == 1
        assert int(gaps["gap_missing"].iloc[0]) == 2

    def test_trading_day_gap_flagged(self):
        """Daily bars Friday -> Tuesday skip Monday, a trading day."""
        df = _ohlcv(["2026-01-09", "2026-01-13"])
        gaps = detect_gaps(df, "1d", calendar=WeekendClosedCalendar())
        assert len(gaps) == 1
        assert int(gaps["gap_missing"].iloc[0]) == 1

    def test_holiday_gap_suppressed(self):
        """Daily bars Wed Dec 31 -> Fri Jan 2 with Jan 1 a holiday."""
        regular = SessionWindow(Session.REGULAR, time(9, 30), time(16, 0))
        schedule = {weekday: (regular,) for weekday in range(5)}
        cal = ExchangeCalendar("America/New_York", schedule, holidays=[date(2026, 1, 1)])
        df = _ohlcv(["2025-12-31", "2026-01-02"])
        assert not detect_gaps(df, "1d").empty  # flagged without a calendar
        assert detect_gaps(df, "1d", calendar=cal).empty


class TestApplyFetchContract:
    """The ``[start, end)`` window and closed-by-default rules (sec 18)."""

    @staticmethod
    def _three_hours() -> pd.DataFrame:
        return _ohlcv(["2024-01-01 00:00", "2024-01-01 01:00", "2024-01-01 02:00"])

    def test_end_inside_a_bar_excludes_that_bar(self):
        out = apply_fetch_contract(
            self._three_hours(),
            "1h",
            pd.Timestamp("2024-01-01 00:00", tz="UTC"),
            pd.Timestamp("2024-01-01 01:30", tz="UTC"),
            closed_only=False,
        )
        assert out["timestamp"].tolist() == [pd.Timestamp("2024-01-01 00:00", tz="UTC")]

    def test_end_is_exclusive(self):
        out = apply_fetch_contract(
            self._three_hours(),
            "1h",
            pd.Timestamp("2024-01-01 00:00", tz="UTC"),
            pd.Timestamp("2024-01-01 02:00", tz="UTC"),
            closed_only=False,
        )
        assert out["timestamp"].tolist() == [
            pd.Timestamp("2024-01-01 00:00", tz="UTC"),
            pd.Timestamp("2024-01-01 01:00", tz="UTC"),
        ]

    def test_start_is_inclusive(self):
        out = apply_fetch_contract(
            self._three_hours(),
            "1h",
            pd.Timestamp("2024-01-01 01:00", tz="UTC"),
            pd.Timestamp("2024-01-01 04:00", tz="UTC"),
            closed_only=False,
        )
        assert out["timestamp"].iloc[0] == pd.Timestamp("2024-01-01 01:00", tz="UTC")

    def test_forming_bar_is_dropped_by_default(self):
        now = pd.Timestamp("2024-01-01 02:30", tz="UTC")
        start = pd.Timestamp("2024-01-01 00:00", tz="UTC")
        end = pd.Timestamp("2024-01-01 04:00", tz="UTC")
        closed = apply_fetch_contract(self._three_hours(), "1h", start, end, now=now)
        assert len(closed) == 2
        assert closed["is_closed"].all()

        live = apply_fetch_contract(
            self._three_hours(), "1h", start, end, now=now, closed_only=False
        )
        assert len(live) == 3
        assert not live["is_closed"].iloc[-1]

    def test_empty_window_returns_empty_frame(self):
        out = apply_fetch_contract(
            self._three_hours(),
            "1h",
            pd.Timestamp("2024-01-02 00:00", tz="UTC"),
            pd.Timestamp("2024-01-02 01:00", tz="UTC"),
        )
        assert out.empty

    def test_negative_prices_rejected_unless_opted_in(self):
        df = self._three_hours()
        df.loc[0, "open"] = -1.0
        df.loc[0, "low"] = -2.0  # keep open inside [low, high]
        start = pd.Timestamp("2024-01-01 00:00", tz="UTC")
        end = pd.Timestamp("2024-01-01 01:00", tz="UTC")
        with pytest.raises(DataValidationError, match="negative"):
            apply_fetch_contract(df, "1h", start, end, closed_only=False)
        apply_fetch_contract(df, "1h", start, end, closed_only=False, allow_negative_prices=True)


class TestBarCloseTimes:
    """Period closes follow sessions and the calendar, not a fixed duration."""

    def test_monthly_bar_closes_on_next_month_start(self):
        closes = bar_close_times(pd.Series(pd.to_datetime(["2024-02-01"], utc=True)), "1mo")
        assert closes.iloc[0] == pd.Timestamp("2024-03-01", tz="UTC")

    def test_intraday_bar_is_clipped_to_the_session_close(self):
        ts = pd.Series(pd.to_datetime(["2024-01-02 20:00", "2024-01-02 20:30"], utc=True))
        closes = bar_close_times(ts, "1h", calendar=WeekendClosedCalendar(), session=XNYS_REGULAR)
        # The 16:00 Eastern close is 21:00 UTC; an hourly bar opening at 20:30
        # would nominally run to 21:30.
        assert closes.tolist() == [
            pd.Timestamp("2024-01-02 21:00", tz="UTC"),
            pd.Timestamp("2024-01-02 21:00", tz="UTC"),
        ]

    def test_daily_bar_closes_at_the_last_session_end(self):
        closes = bar_close_times(
            pd.Series(pd.to_datetime(["2024-01-02 14:30"], utc=True)),
            "1d",
            calendar=WeekendClosedCalendar(),
            session=XNYS_REGULAR,
        )
        assert closes.iloc[0] == pd.Timestamp("2024-01-02 21:00", tz="UTC")

    def test_weekly_bar_closes_with_fridays_session(self):
        closes = bar_close_times(
            pd.Series(pd.to_datetime(["2024-01-02 14:30"], utc=True)),
            "1w",
            calendar=WeekendClosedCalendar(),
            session=XNYS_REGULAR,
        )
        assert closes.iloc[0] == pd.Timestamp("2024-01-05 21:00", tz="UTC")


class TestApplyFetchContractCalendarAware:
    """The contract uses the real period close for session and period bars."""

    def test_equity_daily_bar_is_closed_at_session_close(self):
        out = apply_fetch_contract(
            _ohlcv(["2024-01-02 14:30"]),
            "1d",
            pd.Timestamp("2024-01-02 14:30", tz="UTC"),
            pd.Timestamp("2024-01-02 21:00", tz="UTC"),
            now=pd.Timestamp("2024-01-02 21:00", tz="UTC"),
            calendar=WeekendClosedCalendar(),
            session=XNYS_REGULAR,
        )
        assert len(out) == 1
        assert out["is_closed"].iloc[0]

    def test_equity_daily_bar_is_excluded_before_session_close(self):
        out = apply_fetch_contract(
            _ohlcv(["2024-01-02 14:30"]),
            "1d",
            pd.Timestamp("2024-01-02 14:30", tz="UTC"),
            pd.Timestamp("2024-01-02 20:59", tz="UTC"),
            now=pd.Timestamp("2024-01-02 21:00", tz="UTC"),
            calendar=WeekendClosedCalendar(),
            session=XNYS_REGULAR,
        )
        assert out.empty

    def test_monthly_bar_is_kept_when_the_request_ends_at_month_start(self):
        out = apply_fetch_contract(
            _ohlcv(["2024-02-01"]),
            "1mo",
            pd.Timestamp("2024-02-01", tz="UTC"),
            pd.Timestamp("2024-03-01", tz="UTC"),
            now=pd.Timestamp("2024-03-01 00:05", tz="UTC"),
        )
        assert len(out) == 1
        assert out["is_closed"].iloc[0]

    def test_equity_weekly_bar_is_kept_through_the_weekend(self):
        out = apply_fetch_contract(
            _ohlcv(["2024-01-02 14:30"]),
            "1w",
            pd.Timestamp("2024-01-02 14:30", tz="UTC"),
            pd.Timestamp("2024-01-08", tz="UTC"),
            now=pd.Timestamp("2024-01-08", tz="UTC"),
            calendar=WeekendClosedCalendar(),
            session=XNYS_REGULAR,
        )
        assert len(out) == 1
        assert out["is_closed"].iloc[0]


class TestAnchorPeriodBars:
    """Calendar-date period bars are relabelled at their session open."""

    def test_daily_calendar_date_moves_to_session_open(self):
        out = anchor_period_bars(
            _ohlcv(["2024-01-02 05:00"]),
            "1d",
            calendar=WeekendClosedCalendar(),
            session=XNYS_REGULAR,
        )
        assert out.loc[0, "timestamp"] == pd.Timestamp("2024-01-02 14:30", tz="UTC")
        assert out.attrs["bar_anchor"] == "session_open"

    def test_without_a_calendar_bars_keep_their_label(self):
        out = anchor_period_bars(_ohlcv(["2024-01-02 05:00"]), "1d")
        assert out.loc[0, "timestamp"] == pd.Timestamp("2024-01-02 05:00", tz="UTC")
        assert out.attrs["bar_anchor"] == "calendar_date"

    def test_intraday_bars_are_untouched(self):
        out = anchor_period_bars(_ohlcv(["2024-01-02 14:30"]), "1h", session=XNYS_REGULAR)
        assert out.loc[0, "timestamp"] == pd.Timestamp("2024-01-02 14:30", tz="UTC")
