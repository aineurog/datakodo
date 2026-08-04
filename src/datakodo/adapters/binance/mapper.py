"""Binance raw response → canonical schema normalization.

All normalization uses vectorized operations via pandas/polars.
"""

import pandas as pd

from datakodo.core.enums import AssetClass, InstrumentType
from datakodo.core.schemas import (
    CryptoFundamentals,
    Fundamentals,
    OrderBook,
    OrderBookLevel,
    Trade,
)


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


def map_fundamentals(ticker: dict, info: dict | None = None) -> Fundamentals:
    """Convert raw Binance 24h ticker + exchange info into canonical ``Fundamentals``.

    ``ticker`` supplies live price/volume stats and a ``closeTime`` timestamp.
    ``info`` (from exchange info) supplies reference data — base/quote assets,
    status, permissions, and spot/margin trading flags — captured in the
    asset-class-specific ``CryptoFundamentals`` block.
    """
    info = info or {}
    close_ms = ticker.get("closeTime")
    return Fundamentals(
        symbol=ticker.get("symbol", ""),
        asset_class=AssetClass.CRYPTO,
        instrument_type=InstrumentType.SPOT,
        currency=(
            ticker.get("quoteAsset")
            or info.get("quoteAsset")
            or _quote_asset(ticker.get("symbol", ""))
        ),
        exchange="Binance",
        latest_price=_to_opt_float(ticker.get("lastPrice")),
        price_change_24h=_to_opt_float(ticker.get("priceChange")),
        open_24h=_to_opt_float(ticker.get("openPrice")),
        high_24h=_to_opt_float(ticker.get("highPrice")),
        low_24h=_to_opt_float(ticker.get("lowPrice")),
        volume_24h=_to_opt_float(ticker.get("volume")),
        quote_volume_24h=_to_opt_float(ticker.get("quoteVolume")),
        timestamp=pd.Timestamp(close_ms, unit="ms", tz="UTC").to_pydatetime()
        if close_ms is not None
        else None,
        crypto=CryptoFundamentals(
            base_asset=info.get("baseAsset", "") or _base_asset(ticker.get("symbol", "")),
            quote_asset=info.get("quoteAsset", "") or _quote_asset(ticker.get("symbol", "")),
            status=info.get("status", "TRADING"),  # TRADING / BREAK / HALT
            is_spot_trading_allowed=info.get("isSpotTradingAllowed"),
            is_margin_trading_allowed=info.get("isMarginTradingAllowed"),
            permissions=info.get("permissions") or ["SPOT"],
            asset_class=AssetClass.CRYPTO,
            instrument_type=InstrumentType.SPOT,
        ),
    )


def _to_opt_float(value) -> float | None:
    """Parse a numeric string (Binance returns floats as strings) to float/None."""
    if value in (None, "", "-"):
        return None
    return float(value)


def _base_asset(symbol: str) -> str:
    """Derive the base asset from a symbol such as ``BTCUSDT`` → ``BTC``."""
    for quote in ("USDT", "BUSD", "USDC", "BTC", "ETH", "BNB"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


def _quote_asset(symbol: str) -> str:
    """Derive the quote asset from a symbol such as ``BTCUSDT`` → ``USDT``."""
    for quote in ("USDT", "BUSD", "USDC", "BTC", "ETH", "BNB"):
        if symbol.endswith(quote):
            return quote
    return ""


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
        bids=[OrderBookLevel(price=float(row[0]), size=float(row[1])) for row in bids_raw],
        asks=[OrderBookLevel(price=float(row[0]), size=float(row[1])) for row in asks_raw],
    )
