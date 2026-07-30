"""Alpaca raw response → canonical schema normalization.

All normalization uses vectorized operations via pandas/polars.
"""

import pandas as pd

from datakodo.core.schemas import Trade


def map_ohlcv(raw: list) -> pd.DataFrame:
    """Convert raw Alpaca bars into a DataFrame of canonical OHLCV rows.

    Alpaca bar fields: t (timestamp), o (open), h (high), l (low),
    c (close), v (volume).

    Returns a DataFrame with columns matching the OHLCV schema.
    """
    if not raw:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "session"]
        )

    rows = [
        {
            "timestamp": pd.Timestamp(b.t, unit="s", tz="UTC"),
            "open": float(b.o),
            "high": float(b.h),
            "low": float(b.l),
            "close": float(b.c),
            "volume": float(b.v),
            "session": "regular",
        }
        for b in raw
    ]
    return pd.DataFrame(rows)


def map_trades(raw: dict) -> Trade:
    """Convert a raw Alpaca trade message into a canonical Trade."""
    return Trade(
        timestamp=pd.Timestamp(raw["t"], unit="s", tz="UTC"),
        price=float(raw["p"]),
        size=float(raw["s"]),
        side=str(raw.get("tks", "")),
    )
