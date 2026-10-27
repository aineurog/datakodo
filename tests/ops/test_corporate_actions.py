"""Corporate actions adjustment tests."""

import pandas as pd
import pytest

from datakodo.ops.corporate_actions import (
    Dividend,
    Split,
    adjust_ohlcv_for_dividends,
    adjust_ohlcv_for_splits,
    apply_corporate_actions,
)


@pytest.fixture
def ohlcv_df():
    """Small OHLCV DataFrame 2024-01-01 to 2024-01-05, oldest first."""
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=5, freq="1D", tz="UTC"),
            "open": [100.0, 102.0, 104.0, 106.0, 108.0],
            "high": [102.0, 104.0, 106.0, 108.0, 110.0],
            "low": [99.0, 101.0, 103.0, 105.0, 107.0],
            "close": [101.0, 103.0, 105.0, 107.0, 109.0],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0, 1000.0],
        }
    )


class TestAdjustForSplits:
    def test_no_splits_returns_copy(self, ohlcv_df):
        result = adjust_ohlcv_for_splits(ohlcv_df, [])
        assert result.equals(ohlcv_df)
        assert result is not ohlcv_df

    def test_single_split_adjusts_prices(self, ohlcv_df):
        splits = [{"date": "2024-01-04", "ratio": 2.0}]
        result = adjust_ohlcv_for_splits(ohlcv_df, splits)

        # Rows before 2024-01-04 should have prices halved
        assert result.loc[0, "close"] == 50.5
        # Row on/after 2024-01-04 should be unchanged
        assert result.loc[3, "close"] == 107.0

    def test_single_split_adjusts_volume(self, ohlcv_df):
        splits = [{"date": "2024-01-04", "ratio": 2.0}]
        result = adjust_ohlcv_for_splits(ohlcv_df, splits)

        # Volume before split should double
        assert result.loc[0, "volume"] == 2000.0
        # Volume after split should be unchanged
        assert result.loc[3, "volume"] == 1000.0


class TestAdjustForDividends:
    def test_no_dividends_returns_copy(self, ohlcv_df):
        result = adjust_ohlcv_for_dividends(ohlcv_df, [])
        assert result.equals(ohlcv_df)
        assert result is not ohlcv_df

    def test_single_dividend_adjusts_prices(self, ohlcv_df):
        dividends = [{"ex_date": "2024-01-04", "amount": 1.0}]
        result = adjust_ohlcv_for_dividends(ohlcv_df, dividends)

        # Prices before ex-date should be adjusted down
        assert result.loc[0, "close"] < 101.0
        # Prices on/after ex-date unchanged
        assert result.loc[3, "close"] == 107.0


class TestApplyCorporateActions:
    def test_no_actions_adds_unit_factors(self, ohlcv_df):
        result = apply_corporate_actions(ohlcv_df)
        assert result["price_factor"].tolist() == [1.0] * 5
        assert result["volume_factor"].tolist() == [1.0] * 5
        assert result["close"].tolist() == ohlcv_df["close"].tolist()
        assert result.attrs["price_adjustment"] == "raw"

    def test_splits_then_dividends_single_pass(self, ohlcv_df):
        result = apply_corporate_actions(
            ohlcv_df,
            splits=[Split("2024-01-03", 2.0)],
            dividends=[Dividend("2024-01-04", 1.0)],
            adjust_dividends=True,
        )
        # Split applies to Jan 1-2; dividend factor uses the Jan 3 close (105).
        dividend_factor = (105.0 - 1.0) / 105.0
        assert result.loc[0, "price_factor"] == pytest.approx(0.5 * dividend_factor)
        assert result.loc[1, "price_factor"] == pytest.approx(0.5 * dividend_factor)
        assert result.loc[2, "price_factor"] == pytest.approx(dividend_factor)
        assert result.loc[3, "price_factor"] == pytest.approx(1.0)
        assert result.attrs["price_adjustment"] == "split_and_dividend"

    def test_split_inverse_adjusts_volume_and_dividend_does_not(self, ohlcv_df):
        result = apply_corporate_actions(
            ohlcv_df,
            splits=[Split("2024-01-03", 2.0)],
            dividends=[Dividend("2024-01-04", 1.0)],
            adjust_dividends=True,
        )
        assert result.loc[0, "volume_factor"] == 2.0
        assert result.loc[1, "volume_factor"] == 2.0
        assert result.loc[2, "volume_factor"] == 1.0
        assert result.loc[0, "volume"] == 2000.0
        assert result.loc[2, "volume"] == 1000.0

    def test_same_day_split_is_applied_before_dividend(self, ohlcv_df):
        result = apply_corporate_actions(
            ohlcv_df,
            splits=[Split("2024-01-04", 2.0)],
            dividends=[Dividend("2024-01-04", 1.0)],
            adjust_dividends=True,
        )
        # Jan 3 close 105 is pre-split; the split halves it to 52.5 before the
        # dividend factor is measured.
        dividend_factor = (52.5 - 1.0) / 52.5
        assert result.loc[0, "price_factor"] == pytest.approx(0.5 * dividend_factor)
        assert result.loc[3, "price_factor"] == pytest.approx(1.0)

    def test_raw_data_is_reconstructible(self, ohlcv_df):
        result = apply_corporate_actions(
            ohlcv_df,
            splits=[Split("2024-01-03", 2.0), Split("2024-01-05", 1.5)],
            dividends=[Dividend("2024-01-04", 1.0)],
            adjust_dividends=True,
        )
        raw_close = result["close"] / result["price_factor"]
        raw_volume = result["volume"] / result["volume_factor"]
        assert raw_close.tolist() == pytest.approx(ohlcv_df["close"].tolist())
        assert raw_volume.tolist() == pytest.approx(ohlcv_df["volume"].tolist())

    def test_dividends_are_opt_in(self, ohlcv_df):
        with pytest.raises(ValueError, match="opt-in"):
            apply_corporate_actions(ohlcv_df, dividends=[Dividend("2024-01-04", 1.0)])

    def test_dividend_adjustment_leaves_volume_untouched(self, ohlcv_df):
        result = apply_corporate_actions(
            ohlcv_df,
            dividends=[Dividend("2024-01-04", 1.0)],
            adjust_dividends=True,
        )
        assert result["volume"].tolist() == ohlcv_df["volume"].tolist()
        assert result.loc[0, "price_factor"] < 1.0
        assert result.attrs["price_adjustment"] == "split_and_dividend"

    def test_invalid_split_ratio_raises(self, ohlcv_df):
        with pytest.raises(ValueError, match="positive"):
            apply_corporate_actions(ohlcv_df, splits=[Split("2024-01-03", 0.0)])

    def test_negative_dividend_raises(self, ohlcv_df):
        with pytest.raises(ValueError, match="non-negative"):
            apply_corporate_actions(
                ohlcv_df,
                dividends=[Dividend("2024-01-04", -1.0)],
                adjust_dividends=True,
            )

    def test_unsorted_input_raises(self, ohlcv_df):
        with pytest.raises(ValueError, match="sorted"):
            apply_corporate_actions(ohlcv_df.iloc[::-1], splits=[Split("2024-01-03", 2.0)])
