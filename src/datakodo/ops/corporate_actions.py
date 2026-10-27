"""Corporate action adjustment logic.

Handles split and dividend adjustments on OHLCV data. Separate from
validation.py because price adjustment is non-trivial transformation
logic, not just a sanity check on data quality.

Adjustment is an explicit pipeline step, never a fetch side effect. The
pipeline starts from raw prices, applies splits before dividends, and records
the cumulative factors so the raw series can be reconstructed:

    raw_price = adjusted_price / price_factor
    raw_volume = adjusted_volume / volume_factor
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd

from datakodo.core.enums import PriceAdjustment

DateLike = date | datetime | str

_PRICE_COLUMNS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class Split:
    """A stock split effective on ``date`` (``ratio`` 2.0 is a 2:1 split)."""

    date: DateLike
    ratio: float


@dataclass(frozen=True)
class Dividend:
    """A cash dividend per share with ex-date ``ex_date``."""

    ex_date: DateLike
    amount: float


def adjust_ohlcv_for_splits(df: pd.DataFrame, split_history: list[dict]) -> pd.DataFrame:
    """Back-adjust OHLCV prices for stock splits.

    Args:
        df: OHLCV DataFrame with columns timestamp, open, high, low,
            close, volume. Must be sorted oldest-first.
        split_history: List of splits, each with ``date`` and
            ``ratio`` (e.g. 2.0 for a 2:1 split).

    Returns:
        A new DataFrame with prices adjusted backward from the most
        recent split. Volume is adjusted inversely (multiplied by the
        ratio for dates before the split).
    """
    if not split_history:
        return df.copy()

    df = df.copy()
    splits = sorted(split_history, key=lambda s: s["date"], reverse=True)

    for split in splits:
        split_date = _align_timestamp(split["date"], df["timestamp"])
        ratio = float(split["ratio"])

        mask = df["timestamp"] < split_date
        price_cols = ["open", "high", "low", "close"]

        df.loc[mask, price_cols] = df.loc[mask, price_cols] / ratio
        df.loc[mask, "volume"] = df.loc[mask, "volume"] * ratio

    return df


def adjust_ohlcv_for_dividends(df: pd.DataFrame, dividend_history: list[dict]) -> pd.DataFrame:
    """Back-adjust OHLCV prices for cash dividends.

    Uses the standard approach of scaling prices by (close_before -
    dividend) / close_before on the ex-date and all prior dates.

    Args:
        df: OHLCV DataFrame with columns timestamp, open, high, low,
            close. Must be sorted oldest-first.
        dividend_history: List of dividends, each with ``ex_date`` and
            ``amount`` (cash dividend per share).

    Returns:
        A new DataFrame with dividend-adjusted prices.
    """
    if not dividend_history:
        return df.copy()

    df = df.copy()
    divs = sorted(dividend_history, key=lambda d: d["ex_date"], reverse=True)

    for div in divs:
        ex_date = _align_timestamp(div["ex_date"], df["timestamp"])
        amount = float(div["amount"])

        mask = df["timestamp"] < ex_date
        if not mask.any():
            continue

        close_before = df.loc[mask, "close"].iloc[-1]
        if close_before == 0:
            continue

        factor = (close_before - amount) / close_before
        price_cols = ["open", "high", "low", "close"]
        df.loc[mask, price_cols] = df.loc[mask, price_cols] * factor

    return df


def apply_corporate_actions(
    df: pd.DataFrame,
    *,
    splits: Sequence[Split | Mapping] = (),
    dividends: Sequence[Dividend | Mapping] = (),
    adjust_dividends: bool = False,
) -> pd.DataFrame:
    """Apply splits and dividends to a raw OHLCV frame in one pass.

    Splits are applied before dividends, even when both share an ex-date, so a
    dividend amount is measured against the post-split close. Both are computed
    from the raw frame, which prevents double adjustment: the output is exactly
    ``raw * price_factor`` and ``raw_volume * volume_factor``.

    Splits inverse-adjust volume (a 2:1 split doubles pre-split share counts).
    Dividends never touch volume and must be opted into with
    ``adjust_dividends=True``. The frame carries ``price_factor`` and
    ``volume_factor`` columns plus a ``price_adjustment`` attr, so raw data is
    reconstructible by dividing the adjusted values by the corresponding factor.

    Raises ``ValueError`` for unsorted timestamps, non-positive split ratios,
    negative dividends, or dividends supplied without opting in.
    """
    required = {"timestamp", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame is missing required column(s): {sorted(missing)}.")
    if not df["timestamp"].is_monotonic_increasing:
        raise ValueError("OHLCV timestamps must be sorted oldest-first.")
    if df["timestamp"].duplicated().any():
        raise ValueError("OHLCV timestamps must be unique.")
    if dividends and not adjust_dividends:
        raise ValueError("Dividends are opt-in; pass adjust_dividends=True to apply them.")

    timestamps = df["timestamp"]
    n = len(df)
    price_factor = np.ones(n, dtype=float)
    volume_factor = np.ones(n, dtype=float)

    events: list[tuple[pd.Timestamp, int, str, float]] = []
    split_events: list[tuple[pd.Timestamp, float]] = []
    for item in splits:
        split_date, ratio = _split_fields(item)
        if ratio <= 0:
            raise ValueError(f"Split ratio must be positive, got {ratio}.")
        split_ts = _align_timestamp(split_date, timestamps)
        events.append((split_ts, 0, "split", ratio))
        split_events.append((split_ts, ratio))
    for dividend in dividends:
        ex_date, amount = _dividend_fields(dividend)
        if amount < 0:
            raise ValueError(f"Dividend amount must be non-negative, got {amount}.")
        events.append((_align_timestamp(ex_date, timestamps), 1, "dividend", amount))

    events.sort(key=lambda event: (event[0], event[1]))
    raw_close = df["close"].to_numpy(dtype=float)

    for event_ts, _, kind, value in events:
        mask = (timestamps < event_ts).to_numpy()
        if kind == "split":
            price_factor[mask] /= value
            volume_factor[mask] *= value
            continue
        if not mask.any():
            continue
        index = int(np.flatnonzero(mask)[-1])
        close_before = raw_close[index]
        # "Splits before dividends": a same-day split changes the share basis
        # of the close that the dividend factor is measured against.
        for split_ts, ratio in split_events:
            if timestamps.iloc[index] < split_ts <= event_ts:
                close_before /= ratio
        if close_before == 0:
            continue
        price_factor[mask] *= (close_before - value) / close_before

    out = df.copy()
    for column in _PRICE_COLUMNS:
        out[column] = out[column].astype(float) * price_factor
    if "volume" in out.columns:
        out["volume"] = out["volume"].astype(float) * volume_factor
    out["price_factor"] = price_factor
    out["volume_factor"] = volume_factor

    if adjust_dividends and dividends:
        out.attrs["price_adjustment"] = PriceAdjustment.SPLIT_AND_DIVIDEND.value
    elif splits:
        out.attrs["price_adjustment"] = PriceAdjustment.SPLIT.value
    else:
        out.attrs["price_adjustment"] = PriceAdjustment.RAW.value
    return out


def _split_fields(item: Split | Mapping) -> tuple[DateLike, float]:
    if isinstance(item, Mapping):
        return item["date"], float(item["ratio"])
    return item.date, float(item.ratio)


def _dividend_fields(item: Dividend | Mapping) -> tuple[DateLike, float]:
    if isinstance(item, Mapping):
        return item["ex_date"], float(item["amount"])
    return item.ex_date, float(item.amount)


def _align_timestamp(value: DateLike, timestamps: pd.Series) -> pd.Timestamp:
    """Match an event date to the frame's timezone convention."""
    ts = pd.Timestamp(value)
    timezone = timestamps.dt.tz
    if timezone is not None:
        return ts.tz_localize(timezone) if ts.tz is None else ts.tz_convert(timezone)
    return ts.tz_localize(None) if ts.tz is not None else ts
