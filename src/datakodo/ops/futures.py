"""Continuous futures construction.

Stitches individual contract frames into one series, choosing the front
contract on a calendar schedule and back-adjusting the price history across
rolls. Adjustment is an explicit pipeline step, never a fetch side effect.

Ratio adjustment multiplies prices before a roll by ``new_close / old_close``;
difference adjustment adds ``new_close - old_close``. Both preserve the
frame's OHLC relationships, and ``price_factor`` / ``price_offset`` allow the
raw prices to be reconstructed:

    raw_price = (adjusted_price - price_offset) / price_factor

The contract multiplier never touches prices. It is applied only to notional
metrics (``notional``) when requested.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd

from datakodo.core.enums import RollRule
from datakodo.core.exceptions import NotSupportedError

DateLike = date | datetime | str
Adjustment = str

_PRICE_COLUMNS = ("open", "high", "low", "close")
_REQUIRED_COLUMNS = {"timestamp", *_PRICE_COLUMNS}
_ADJUSTMENTS = ("ratio", "difference", "none")


@dataclass(frozen=True)
class ContinuousFutures:
    """A stitched front-contract series and the rolls that produced it."""

    frame: pd.DataFrame
    rolls: pd.DataFrame
    multiplier: float = 1.0


def build_continuous_futures(
    contracts: Mapping[str, pd.DataFrame],
    *,
    expiries: Mapping[str, DateLike] | None = None,
    first_notice_dates: Mapping[str, DateLike] | None = None,
    roll_rule: RollRule | str = RollRule.CALENDAR,
    roll_dates: Sequence[DateLike] | None = None,
    roll_offset_days: int = 0,
    adjustment: Adjustment = "ratio",
    multiplier: float = 1.0,
    include_notional: bool = False,
) -> ContinuousFutures:
    """Build a continuous front-month futures series.

    ``contracts`` maps each contract symbol to its raw OHLCV frame. Contracts
    are ordered by expiry when every contract has one, then by first-notice
    date, otherwise in mapping order.

    An explicit ``roll_dates`` sequence (one entry per roll) takes precedence
    over ``roll_rule``. Otherwise ``CALENDAR`` derives each roll from the
    contract's first-notice date when supplied, falling back to its expiry,
    shifted by ``roll_offset_days`` (negative rolls earlier). Bars strictly
    before a roll date belong to the outgoing contract; bars on or after it
    belong to the incoming contract.

    Every roll is recorded in the returned ``rolls`` frame, including its raw
    prices, ratio, difference, and cumulative adjustment. ``multiplier`` is
    applied to the optional ``notional`` column (``close * volume *
    multiplier``) and never to OHLC prices.
    """
    if not contracts:
        raise ValueError("At least one contract frame is required.")
    if adjustment not in _ADJUSTMENTS:
        raise ValueError(f"adjustment must be one of {_ADJUSTMENTS}, got {adjustment!r}.")
    if multiplier <= 0:
        raise ValueError(f"multiplier must be positive, got {multiplier}.")

    normalized = {symbol: _normalize_contract(symbol, frame) for symbol, frame in contracts.items()}
    timezone = _common_timezone(normalized)
    symbols = _ordered_symbols(normalized, expiries, first_notice_dates, timezone)
    roll_timestamps = _roll_schedule(
        symbols,
        expiries=expiries,
        first_notice_dates=first_notice_dates,
        roll_rule=roll_rule,
        roll_dates=roll_dates,
        roll_offset_days=roll_offset_days,
        timezone=timezone,
    )

    continuous = _stitch(symbols, normalized, roll_timestamps)
    prices = _roll_prices(symbols, normalized, roll_timestamps)

    price_factor = np.ones(len(continuous), dtype=float)
    price_offset = np.zeros(len(continuous), dtype=float)
    records: list[dict] = []
    cumulative_ratio = 1.0
    cumulative_difference = 0.0

    timestamps = continuous["timestamp"]
    for roll_ts, old_symbol, new_symbol, old_close, new_close in prices:
        mask = (timestamps < roll_ts).to_numpy()
        ratio = new_close / old_close if old_close > 0 else float("nan")
        difference = new_close - old_close
        if adjustment == "ratio" and (old_close <= 0 or new_close <= 0):
            raise ValueError(
                f"Cannot ratio-adjust roll {old_symbol} -> {new_symbol} at "
                f"{roll_ts.isoformat()}: closes are {old_close!r} and {new_close!r}."
            )
        if adjustment == "ratio":
            price_factor[mask] *= ratio
        elif adjustment == "difference":
            price_offset[mask] += difference
        cumulative_ratio *= ratio
        cumulative_difference += difference
        records.append(
            {
                "date": roll_ts,
                "from_contract": old_symbol,
                "to_contract": new_symbol,
                "from_price": old_close,
                "to_price": new_close,
                "ratio": ratio,
                "difference": difference,
                "cumulative_ratio": cumulative_ratio,
                "cumulative_difference": cumulative_difference,
            }
        )

    for column in _PRICE_COLUMNS:
        continuous[column] = continuous[column].astype(float) * price_factor + price_offset
    continuous["price_factor"] = price_factor
    continuous["price_offset"] = price_offset

    if include_notional:
        if "volume" not in continuous.columns:
            raise ValueError("include_notional requires a volume column.")
        continuous["notional"] = (
            continuous["close"] * continuous["volume"].astype(float) * multiplier
        )

    continuous.attrs["roll_adjustment"] = adjustment
    continuous.attrs["multiplier"] = multiplier
    continuous.attrs["price_adjustment"] = "raw" if adjustment == "none" else "continuous"

    rolls = pd.DataFrame(
        records,
        columns=[
            "date",
            "from_contract",
            "to_contract",
            "from_price",
            "to_price",
            "ratio",
            "difference",
            "cumulative_ratio",
            "cumulative_difference",
        ],
    )
    return ContinuousFutures(frame=continuous, rolls=rolls, multiplier=multiplier)


def _normalize_contract(symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
    missing = _REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"Contract {symbol!r} is missing required column(s): {sorted(missing)}.")
    if not frame["timestamp"].is_monotonic_increasing:
        raise ValueError(f"Contract {symbol!r} timestamps must be sorted oldest-first.")
    if frame["timestamp"].duplicated().any():
        raise ValueError(f"Contract {symbol!r} timestamps must be unique.")
    return frame.copy()


def _common_timezone(contracts: Mapping[str, pd.DataFrame]):
    zones = {frame["timestamp"].dt.tz for frame in contracts.values()}
    if len(zones) > 1:
        raise ValueError("All contract timestamps must share one timezone convention.")
    return zones.pop()


def _ordered_symbols(
    contracts: Mapping[str, pd.DataFrame],
    expiries: Mapping[str, DateLike] | None,
    first_notice_dates: Mapping[str, DateLike] | None,
    timezone,
) -> list[str]:
    symbols = list(contracts)
    if expiries is not None and all(symbol in expiries for symbol in symbols):
        return sorted(symbols, key=lambda symbol: _align(expiries[symbol], timezone))
    if first_notice_dates is not None and all(symbol in first_notice_dates for symbol in symbols):
        return sorted(symbols, key=lambda symbol: _align(first_notice_dates[symbol], timezone))
    return symbols


def _roll_schedule(
    symbols: Sequence[str],
    *,
    expiries: Mapping[str, DateLike] | None,
    first_notice_dates: Mapping[str, DateLike] | None,
    roll_rule: RollRule | str,
    roll_dates: Sequence[DateLike] | None,
    roll_offset_days: int,
    timezone,
) -> list[pd.Timestamp]:
    if len(symbols) == 1:
        return []
    if roll_dates is not None:
        timestamps = [_align(value, timezone) for value in roll_dates]
        if len(timestamps) != len(symbols) - 1:
            raise ValueError(
                f"roll_dates needs {len(symbols) - 1} entries for {len(symbols)} "
                f"contracts, got {len(timestamps)}."
            )
        _require_increasing(timestamps)
        return timestamps

    rule = RollRule(roll_rule)
    if rule == RollRule.NONE:
        raise ValueError("roll_rule NONE is only valid for a single contract.")
    if rule in (RollRule.VOLUME, RollRule.OPEN_INTEREST):
        raise NotSupportedError(
            f"roll_rule {rule.value!r} is not supported; pass explicit roll_dates."
        )

    timestamps = []
    for symbol in symbols[:-1]:
        anchor = None
        if first_notice_dates is not None and symbol in first_notice_dates:
            anchor = _align(first_notice_dates[symbol], timezone)
        elif expiries is not None and symbol in expiries:
            anchor = _align(expiries[symbol], timezone)
        else:
            raise ValueError(
                f"Contract {symbol!r} needs an expiry or first-notice date to "
                "derive a calendar roll."
            )
        if roll_offset_days:
            anchor = anchor + pd.Timedelta(days=roll_offset_days)
        timestamps.append(anchor)
    _require_increasing(timestamps)
    return timestamps


def _require_increasing(timestamps: Sequence[pd.Timestamp]) -> None:
    if any(later <= earlier for earlier, later in zip(timestamps, timestamps[1:])):
        raise ValueError("Roll dates must be strictly increasing.")


def _stitch(
    symbols: Sequence[str],
    contracts: Mapping[str, pd.DataFrame],
    roll_timestamps: Sequence[pd.Timestamp],
) -> pd.DataFrame:
    bounds: list[pd.Timestamp | None] = [None, *roll_timestamps, None]
    parts: list[pd.DataFrame] = []
    for index, symbol in enumerate(symbols):
        frame = contracts[symbol]
        lower, upper = bounds[index], bounds[index + 1]
        if lower is not None:
            frame = frame[frame["timestamp"] >= lower]
        if upper is not None:
            frame = frame[frame["timestamp"] < upper]
        if frame.empty:
            continue
        part = frame.copy()
        part["contract"] = symbol
        parts.append(part)
    if not parts:
        raise ValueError("The roll schedule leaves no bars in any contract.")
    stitched = pd.concat(parts, ignore_index=True)
    stitched = stitched.sort_values("timestamp", kind="stable").reset_index(drop=True)
    if stitched["timestamp"].duplicated().any():
        raise ValueError("The stitched series contains duplicate timestamps.")
    return stitched


def _roll_prices(
    symbols: Sequence[str],
    contracts: Mapping[str, pd.DataFrame],
    roll_timestamps: Sequence[pd.Timestamp],
) -> list[tuple[pd.Timestamp, str, str, float, float]]:
    prices = []
    for index, roll_ts in enumerate(roll_timestamps):
        old_symbol, new_symbol = symbols[index], symbols[index + 1]
        old = contracts[old_symbol]
        new = contracts[new_symbol]
        old_before = old[old["timestamp"] < roll_ts]
        new_after = new[new["timestamp"] >= roll_ts]
        if old_before.empty or new_after.empty:
            raise ValueError(
                f"Cannot price roll {old_symbol} -> {new_symbol} at "
                f"{roll_ts.isoformat()}: one side has no bars around the roll."
            )
        old_close = float(old_before["close"].iloc[-1])
        new_close = float(new_after["close"].iloc[0])
        prices.append((roll_ts, old_symbol, new_symbol, old_close, new_close))
    return prices


def _align(value: DateLike, timezone) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if timezone is not None:
        return ts.tz_localize(timezone) if ts.tz is None else ts.tz_convert(timezone)
    return ts.tz_localize(None) if ts.tz is not None else ts
