"""IBKR raw response → canonical schema normalization.

All normalization uses vectorized operations via pandas/polars.
"""

import pandas as pd

from datakodo.core.schemas import Trade


def map_ohlcv(raw: list) -> pd.DataFrame:
    """Convert raw IBKR historical bars into a DataFrame of canonical OHLCV rows.

    IBKR bar fields: date, open, high, low, close, volume, barCount,
    average.

    Returns a DataFrame with columns matching the OHLCV schema.
    """
    if not raw:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "session"]
        )

    rows = [
        {
            "timestamp": pd.Timestamp(str(b.date), tz="UTC"),
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": float(b.volume),
            "session": "regular",
        }
        for b in raw
    ]
    return pd.DataFrame(rows)


def map_trades(raw) -> Trade:
    """Convert a raw IBKR tick into a canonical Trade."""
    return Trade(
        timestamp=pd.Timestamp(raw.time, tz="UTC"),
        price=float(raw.price),
        size=float(raw.size),
        side="",
    )
