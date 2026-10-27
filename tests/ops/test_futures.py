"""Continuous futures construction tests."""

import pandas as pd
import pytest

from datakodo.core.enums import RollRule
from datakodo.core.exceptions import NotSupportedError
from datakodo.ops.futures import build_continuous_futures


def _contract(closes, *, start="2024-01-01", volume=10.0) -> pd.DataFrame:
    timestamps = pd.date_range(start, periods=len(closes), freq="1D", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [volume] * len(closes),
            "is_closed": [True] * len(closes),
        }
    )


class TestContinuousFutures:
    def test_ratio_back_adjustment_aligns_the_roll(self):
        result = build_continuous_futures(
            {
                "A": _contract([100.0, 100.0, 100.0]),
                "B": _contract([110.0, 110.0, 110.0], start="2024-01-03"),
            },
            roll_dates=["2024-01-03"],
            adjustment="ratio",
        )
        frame = result.frame
        assert frame["close"].tolist() == pytest.approx([110.0, 110.0, 110.0, 110.0, 110.0])
        assert frame["price_factor"].tolist() == pytest.approx([1.1, 1.1, 1.0, 1.0, 1.0])
        assert frame["price_offset"].tolist() == [0.0] * 5
        assert frame["contract"].tolist() == ["A", "A", "B", "B", "B"]

    def test_difference_back_adjustment_aligns_the_roll(self):
        result = build_continuous_futures(
            {
                "A": _contract([100.0, 100.0, 100.0]),
                "B": _contract([110.0, 110.0, 110.0], start="2024-01-03"),
            },
            roll_dates=["2024-01-03"],
            adjustment="difference",
        )
        frame = result.frame
        assert frame["close"].tolist() == pytest.approx([110.0] * 5)
        assert frame["price_factor"].tolist() == [1.0] * 5
        assert frame["price_offset"].tolist() == pytest.approx([10.0, 10.0, 0.0, 0.0, 0.0])

    def test_no_adjustment_keeps_raw_prices(self):
        result = build_continuous_futures(
            {
                "A": _contract([100.0, 100.0, 100.0]),
                "B": _contract([110.0, 110.0, 110.0], start="2024-01-03"),
            },
            roll_dates=["2024-01-03"],
            adjustment="none",
        )
        assert result.frame["close"].tolist() == [100.0, 100.0, 110.0, 110.0, 110.0]
        assert result.frame.attrs["price_adjustment"] == "raw"

    def test_every_roll_is_recorded(self):
        result = build_continuous_futures(
            {
                "A": _contract([100.0, 100.0, 100.0, 100.0, 100.0]),
                "B": _contract([110.0, 110.0, 110.0, 110.0, 110.0]),
                "C": _contract([120.0, 120.0, 120.0, 120.0, 120.0]),
            },
            roll_dates=["2024-01-02", "2024-01-04"],
        )
        rolls = result.rolls
        assert rolls["from_contract"].tolist() == ["A", "B"]
        assert rolls["to_contract"].tolist() == ["B", "C"]
        assert rolls["ratio"].tolist() == pytest.approx([1.1, 120.0 / 110.0])
        assert rolls["cumulative_ratio"].tolist() == pytest.approx([1.1, 1.1 * (120.0 / 110.0)])

    def test_calendar_roll_from_expiry_with_offset(self):
        a = _contract([100.0] * 12)
        b = _contract([100.0] * 12)
        result = build_continuous_futures(
            {"A": a, "B": b},
            expiries={"A": "2024-01-10", "B": "2024-02-10"},
            roll_offset_days=-2,
        )
        assert result.rolls.loc[0, "date"] == pd.Timestamp("2024-01-08", tz="UTC")
        assert result.frame.loc[result.frame["contract"] == "A", "timestamp"].max() == (
            pd.Timestamp("2024-01-07", tz="UTC")
        )
        assert result.frame.loc[result.frame["contract"] == "B", "timestamp"].min() == (
            pd.Timestamp("2024-01-08", tz="UTC")
        )

    def test_first_notice_date_takes_precedence_over_expiry(self):
        result = build_continuous_futures(
            {"A": _contract([100.0] * 12), "B": _contract([100.0] * 12)},
            expiries={"A": "2024-01-10", "B": "2024-02-10"},
            first_notice_dates={"A": "2024-01-05"},
        )
        assert result.rolls.loc[0, "date"] == pd.Timestamp("2024-01-05", tz="UTC")

    def test_raw_prices_are_reconstructible(self):
        result = build_continuous_futures(
            {
                "A": _contract([100.0, 101.0, 102.0]),
                "B": _contract([110.0, 111.0, 112.0], start="2024-01-03"),
            },
            roll_dates=["2024-01-03"],
            adjustment="ratio",
        )
        raw = (result.frame["close"] - result.frame["price_offset"]) / result.frame["price_factor"]
        # A owns Jan 1-2, B owns Jan 3 onward; A's overlapping Jan 3 bar is excluded.
        assert raw.tolist() == pytest.approx([100.0, 101.0, 110.0, 111.0, 112.0])

    def test_multiplier_only_touches_notional(self):
        contracts = {
            "A": _contract([100.0, 100.0, 100.0]),
            "B": _contract([110.0, 110.0, 110.0], start="2024-01-03"),
        }
        base = build_continuous_futures(contracts, roll_dates=["2024-01-03"])
        scaled = build_continuous_futures(
            contracts, roll_dates=["2024-01-03"], multiplier=50.0, include_notional=True
        )
        assert scaled.frame["close"].tolist() == base.frame["close"].tolist()
        assert scaled.frame["open"].tolist() == base.frame["open"].tolist()
        assert scaled.frame["notional"].tolist() == pytest.approx([110.0 * 10.0 * 50.0] * 5)
        assert scaled.frame.attrs["multiplier"] == 50.0

    def test_notional_requires_volume(self):
        contracts = {
            "A": _contract([100.0, 100.0]).drop(columns="volume"),
            "B": _contract([110.0, 110.0]).drop(columns="volume"),
        }
        with pytest.raises(ValueError, match="volume"):
            build_continuous_futures(contracts, roll_dates=["2024-01-02"], include_notional=True)

    def test_volume_roll_rule_is_not_supported(self):
        with pytest.raises(NotSupportedError):
            build_continuous_futures(
                {"A": _contract([100.0] * 3), "B": _contract([100.0] * 3)},
                roll_rule=RollRule.VOLUME,
            )

    def test_explicit_roll_count_must_match_contracts(self):
        with pytest.raises(ValueError, match="roll_dates needs"):
            build_continuous_futures(
                {"A": _contract([100.0] * 3), "B": _contract([100.0] * 3)},
                roll_dates=["2024-01-02", "2024-01-03"],
            )

    def test_rolls_must_be_increasing(self):
        with pytest.raises(ValueError, match="strictly increasing"):
            build_continuous_futures(
                {
                    "A": _contract([100.0] * 5),
                    "B": _contract([100.0] * 5),
                    "C": _contract([100.0] * 5),
                },
                roll_dates=["2024-01-04", "2024-01-02"],
            )

    def test_calendar_roll_requires_an_anchor(self):
        with pytest.raises(ValueError, match="expiry or first-notice"):
            build_continuous_futures({"A": _contract([100.0] * 3), "B": _contract([100.0] * 3)})

    def test_invalid_adjustment_raises(self):
        with pytest.raises(ValueError, match="adjustment must be"):
            build_continuous_futures(
                {"A": _contract([100.0] * 3), "B": _contract([100.0] * 3)},
                roll_dates=["2024-01-02"],
                adjustment="log",
            )

    def test_missing_bars_around_a_roll_raise(self):
        a = _contract([100.0]).drop(index=0)
        with pytest.raises(ValueError, match="no bars around the roll"):
            build_continuous_futures(
                {"A": a, "B": _contract([110.0, 110.0])},
                roll_dates=["2024-01-02"],
            )
