"""Massive raw response → canonical schema normalization.

The mapper is the single place that converts Massive data into canonical
schemas. It accepts two raw shapes so REST and WebSocket share one mapping:

  * SDK model objects (``massive.Agg``, ``massive.Trade``, ...) from
    ``MassiveREST``, read via attributes;
  * plain dicts (WebSocket messages, ``next_url`` JSON) read via keys.

Field names differ between the two shapes (``agg.open`` vs ``{"o": ...}``),
so a small ``_field`` helper tries attribute names and dict keys.
"""

import pandas as pd

from datakodo.core.schemas import Trade

_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "session"]


def _field(item, *names):
    """Return the first present value among ``names`` on an SDK model or dict."""
    for name in names:
        if isinstance(item, dict):
            if name in item:
                return item[name]
        elif hasattr(item, name):
            return getattr(item, name)
    return None


def _to_timestamp(value):
    """Build a UTC Timestamp, auto-detecting ns vs ms by magnitude."""
    value = value or 0
    unit = "ns" if value >= 10**15 else "ms"
    return pd.Timestamp(value, unit=unit, tz="UTC")


def map_ohlcv(raw: list) -> pd.DataFrame:
    """Convert raw Massive aggregates into a DataFrame of canonical OHLCV rows.

    Accepts either SDK ``Agg``/``FuturesAgg`` models or raw dicts. Massive agg
    shapes:

        SDK Agg:      open, high, low, close, volume, vwap, timestamp(ms)
        SDK Futures:  open, high, low, close, volume, window_start(ns)
        dict:         {"o", "h", "l", "c", "v", "vw", "t"}

    Returns a DataFrame with columns matching the OHLCV schema.
    """
    if not raw:
        return pd.DataFrame(columns=_COLUMNS)

    rows = [
        {
            "timestamp": _to_timestamp(_field(r, "timestamp", "window_start", "t")),
            "open": float(_field(r, "open", "o")),
            "high": float(_field(r, "high", "h")),
            "low": float(_field(r, "low", "l")),
            "close": float(_field(r, "close", "c")),
            "volume": float(_field(r, "volume", "v")),
            "session": "regular",
        }
        for r in raw
    ]
    return pd.DataFrame(rows)


def map_trades(raw) -> Trade:
    """Convert a raw Massive trade into a canonical Trade.

    Accepts either an SDK ``Trade`` model or a raw dict (WebSocket message).
    Timestamps arrive in different units depending on the source: v3 REST
    trades use nanoseconds (``sip_timestamp``), WebSocket messages use
    milliseconds. The unit is detected by magnitude: nanosecond values
    (>= 10^15) are ~1e6x larger than millisecond values for the same date.

    Side is derived from Massive's condition codes for crypto (``1`` =
    sellside, ``2`` = buyside); stock/option conditions are opaque exchange
    codes, so ``side`` stays None for them.
    """
    conditions = _field(raw, "conditions", "c") or []
    if isinstance(conditions, int):
        conditions = [conditions]
    if 2 in conditions:
        side = "buy"
    elif 1 in conditions:
        side = "sell"
    else:
        side = None
    return Trade(
        timestamp=_to_timestamp(_field(raw, "sip_timestamp", "t")),
        price=float(_field(raw, "price", "p")),
        size=float(_field(raw, "size", "s")),
        side=side,
    )


def map_ticks(raw: list) -> list[Trade]:
    """Convert a list of raw Massive trades (SDK models or dicts) into Trades."""
    return [map_trades(t) for t in raw]
