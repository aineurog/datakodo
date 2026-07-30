"""Corporate actions adjustment logic.

Handles split and dividend adjustments on OHLCV data. Separate from
validation.py because price adjustment is non-trivial transformation
logic, not just a sanity check on data quality.
"""

import pandas as pd


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
        split_date = pd.Timestamp(split["date"])
        if split_date.tz is None and df["timestamp"].dt.tz is not None:
            split_date = split_date.tz_localize("UTC")
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
        ex_date = pd.Timestamp(div["ex_date"])
        if ex_date.tz is None and df["timestamp"].dt.tz is not None:
            ex_date = ex_date.tz_localize("UTC")
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
