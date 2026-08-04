"""Binance raw response → canonical schema normalization.

All normalization uses vectorized operations via pandas/polars.
"""

import pandas as pd

from datakodo.core.schemas import OrderBook, OrderBookLevel, Trade


def map_ohlcv(raw: list) -> pd.DataFrame:
    """Convert raw Binance klines into a DataFrame of canonical OHLCV rows.

    Binance kline format:
        [
          open_time, open, high, low, close, volume,
          close_time, quote_volume, trades, taker_buy_base,
          taker_buy_quote, ignore
        ]

    Returns a DataFrame with columns matching the OHLCV schema.
    """
    if not raw:
        cols = ["timestamp", "open", "high", "low", "close", "volume", "session"]
        return pd.DataFrame(columns=cols)

    rows = [
        {
            "timestamp": pd.Timestamp(k[0], unit="ms", tz="UTC"),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "session": "n/a",
        }
        for k in raw
    ]
    return pd.DataFrame(rows)


def map_trades(raw: dict) -> Trade:
    """Convert a single raw Binance trade message into a canonical Trade."""
    return Trade(
        timestamp=pd.Timestamp(raw["T"], unit="ms", tz="UTC"),
        price=float(raw["p"]),
        size=float(raw["q"]),
        side="buy" if raw.get("m", False) else "sell",
    )


def map_ticks(raw: list) -> list[Trade]:
    """Convert a list of raw Binance aggTrade rows into a list of Trade."""
    return [map_trades(t) for t in raw]


def map_orderbook(raw: dict) -> OrderBook:
    """Convert a raw Binance depth snapshot into a canonical OrderBook.

    Binance depth rows are ``[price, quantity]`` pairs under ``bids``/``asks``.
    When the payload carries an exchange event time (``E``, as USD-M futures
    does) that is used; otherwise the timestamp is stamped locally (spot).
    """
    event_ms = raw.get("E") or raw.get("T")
    timestamp = (
        pd.Timestamp(event_ms, unit="ms", tz="UTC")
        if event_ms is not None
        else pd.Timestamp.now().tz_localize("UTC")
    )
    bids_raw = raw.get("bids") or []
    asks_raw = raw.get("asks") or []
    return OrderBook(
        timestamp=timestamp.to_pydatetime(),
        bids=[
            OrderBookLevel(price=float(row[0]), size=float(row[1])) for row in bids_raw
        ],
        asks=[
            OrderBookLevel(price=float(row[0]), size=float(row[1])) for row in asks_raw
        ],
    )
