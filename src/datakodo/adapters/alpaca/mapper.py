"""Alpaca raw response → canonical schema normalization."""

import pandas as pd

from datakodo.core.schemas import resolve_ohlcv_columns
from datakodo.core.timeframe import timeframe_delta


def _utc(value) -> pd.Timestamp:
    """Normalize to a UTC-aware pandas Timestamp."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def map_ohlcv(raw: list, timeframe: str, *, session: str | None = "regular", columns="basic"):
    """SDK bars → canonical OHLCV frame (UTC bar-open ``timestamp``)."""
    base = ["timestamp", "open", "high", "low", "close", "volume", "is_closed"]

    if not raw:
        return pd.DataFrame(
            {
                "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
                "open": pd.Series(dtype="float64"),
                "high": pd.Series(dtype="float64"),
                "low": pd.Series(dtype="float64"),
                "close": pd.Series(dtype="float64"),
                "volume": pd.Series(dtype="float64"),
                "is_closed": pd.Series(dtype="bool"),
            }
        )

    df = pd.DataFrame(
        [
            {
                "timestamp": _utc(b.timestamp),
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
                "vwap": None if b.vwap is None else float(b.vwap),
                "trades_count": None if b.trade_count is None else float(b.trade_count),
                "session": session,
            }
            for b in raw
        ]
    )

    df = (
        df.drop_duplicates(subset="timestamp", keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    for col in ("open", "high", "low", "close", "volume", "vwap", "trades_count"):
        df[col] = df[col].astype("float64")

    df["is_closed"] = df["timestamp"] + timeframe_delta(timeframe) <= pd.Timestamp.now(tz="UTC")

    if columns == "all":
        extras = [c for c in df.columns if c not in base]
        return df[base + extras]
    available = tuple(c for c in ("session", "vwap", "trades_count") if c in df.columns)
    return df[resolve_ohlcv_columns(columns, available)]
