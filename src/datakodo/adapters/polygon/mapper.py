"""Polygon raw response → canonical schema normalization.

All normalization uses vectorized operations via pandas/polars.
"""

import pandas as pd

from datakodo.core.schemas import Trade


def map_ohlcv(raw: list) -> pd.DataFrame:
    """Convert raw Polygon aggregates into a DataFrame of canonical OHLCV rows.

    Polygon agg format:
        {
          "t": unix_ms, "o": open, "h": high, "l": low, "c": close,
          "v": volume, "vw": vwap, "n": trades
        }

    Returns a DataFrame with columns matching the OHLCV schema.
    """
    if not raw:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "session"]
        )

    rows = [
        {
            "timestamp": pd.Timestamp(r["t"], unit="ms", tz="UTC"),
            "open": float(r["o"]),
            "high": float(r["h"]),
            "low": float(r["l"]),
            "close": float(r["c"]),
            "volume": float(r["v"]),
            "session": "regular",
        }
        for r in raw
    ]
    return pd.DataFrame(rows)


def map_trades(raw: dict) -> Trade:
    """Convert a raw Polygon trade message into a canonical Trade."""
    conditions = raw.get("c", [])
    return Trade(
        timestamp=pd.Timestamp(raw["t"], unit="ms", tz="UTC"),
        price=float(raw["p"]),
        size=float(raw["s"]),
        side="buy" if "buy" in str(conditions).lower() else "sell",
    )
