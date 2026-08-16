"""Binance raw response → canonical schema normalization.

Normalization is vectorized: raw rows are converted to columnar frames in
bulk rather than looped over row by row.
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

# Optional OHLCV columns the Binance mapper can produce (design doc sec 3).
BINANCE_OHLCV_EXTRAS: tuple[str, ...] = (
    "close_timestamp",
    "quote_volume",
    "trades_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "vwap",
)

_BASE_OHLCV_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")

# Binance kline field indexes (official docs).
_K_OPEN_TIME = 0
_K_OPEN = 1
_K_HIGH = 2
_K_LOW = 3
_K_CLOSE = 4
_K_VOLUME = 5
_K_CLOSE_TIME = 6
_K_QUOTE_VOLUME = 7
_K_TRADES = 8
_K_TAKER_BUY_BASE = 9
_K_TAKER_BUY_QUOTE = 10


def map_ohlcv(raw: list) -> pd.DataFrame:
    """Convert raw Binance klines into a DataFrame of canonical OHLCV rows.

    Binance kline format:
        [
          open_time, open, high, low, close, volume,
          close_time, quote_volume, trades, taker_buy_base,
          taker_buy_quote, ignore
        ]

    Returns a DataFrame with the base OHLCV columns plus every optional extra
    Binance provides. ``is_closed`` is not set here (it depends on the
    timeframe); the adapter computes it via ``ops.validation.add_is_closed``.
    """
    columns = list(_BASE_OHLCV_COLUMNS) + list(BINANCE_OHLCV_EXTRAS)
    if not raw:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(raw)

    out = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(df[_K_OPEN_TIME], unit="ms", utc=True),
            "open": pd.to_numeric(df[_K_OPEN]),
            "high": pd.to_numeric(df[_K_HIGH]),
            "low": pd.to_numeric(df[_K_LOW]),
            "close": pd.to_numeric(df[_K_CLOSE]),
            "volume": pd.to_numeric(df[_K_VOLUME]),
            "close_timestamp": pd.to_datetime(df[_K_CLOSE_TIME], unit="ms", utc=True),
            "quote_volume": pd.to_numeric(df[_K_QUOTE_VOLUME]),
            "trades_count": pd.to_numeric(df[_K_TRADES]).astype("Int64"),
            "taker_buy_base_volume": pd.to_numeric(df[_K_TAKER_BUY_BASE]),
            "taker_buy_quote_volume": pd.to_numeric(df[_K_TAKER_BUY_QUOTE]),
        }
    )
    volume = pd.to_numeric(df[_K_VOLUME]).replace(0, pd.NA)
    out["vwap"] = pd.to_numeric(df[_K_QUOTE_VOLUME]) / volume
    return out


def map_trades(raw: dict) -> Trade:
    """Convert a single raw Binance trade message into a canonical Trade.

    Binance aggTrade ``m`` is "buyer is the maker". The canonical ``side`` is
    the aggressor/taker side, so ``m=True`` means the seller was the taker
    (``side="sell"``) and ``m=False`` means the buyer was the taker
    (``side="buy"``).
    """
    return Trade(
        timestamp=pd.Timestamp(raw["T"], unit="ms", tz="UTC"),
        price=float(raw["p"]),
        size=float(raw["q"]),
        side="sell" if raw.get("m", False) else "buy",
        trade_id=int(raw["a"]) if "a" in raw else None,
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
    symbol = ticker.get("symbol", "")
    close_ms = ticker.get("closeTime")
    return Fundamentals(
        symbol=symbol,
        name=symbol,
        asset_class=AssetClass.CRYPTO,
        instrument_type=InstrumentType.SPOT,
        currency=ticker.get("quoteAsset") or info.get("quoteAsset") or quote_asset(symbol),
        exchange="Binance",
        as_of=pd.Timestamp(close_ms, unit="ms", tz="UTC").to_pydatetime()
        if close_ms is not None
        else None,
        crypto=CryptoFundamentals(
            base_asset=info.get("baseAsset", "") or base_asset(symbol),
            quote_asset=info.get("quoteAsset", "") or quote_asset(symbol),
            status=info.get("status", "TRADING"),  # TRADING / BREAK / HALT
            is_spot_trading_allowed=info.get("isSpotTradingAllowed"),
            is_margin_trading_allowed=info.get("isMarginTradingAllowed"),
            permissions=info.get("permissions") or ["SPOT"],
            latest_price=_to_opt_float(ticker.get("lastPrice")),
            price_change_24h=_to_opt_float(ticker.get("priceChange")),
            open_24h=_to_opt_float(ticker.get("openPrice")),
            high_24h=_to_opt_float(ticker.get("highPrice")),
            low_24h=_to_opt_float(ticker.get("lowPrice")),
            volume_24h=_to_opt_float(ticker.get("volume")),
            quote_volume_24h=_to_opt_float(ticker.get("quoteVolume")),
        ),
    )


def map_orderbook(raw: dict) -> OrderBook:
    """Convert a raw Binance depth snapshot into a canonical OrderBook.

    Binance depth rows are ``[price, quantity]`` pairs under ``bids``/``asks``.
    When the payload carries an exchange event time (``E``, as USD-M futures
    does) that is used; otherwise the timestamp is stamped locally (spot).
    ``last_update_id`` carries the provider's sequence number for later delta
    reconstruction.
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
        last_update_id=raw.get("lastUpdateId"),
    )


def _to_opt_float(value) -> float | None:
    """Parse a numeric string (Binance returns floats as strings) to float/None."""
    if value in (None, "", "-"):
        return None
    return float(value)


def base_asset(symbol: str) -> str:
    """Derive the base asset from a symbol such as ``BTCUSDT`` → ``BTC``."""
    for quote in ("USDT", "BUSD", "USDC", "BTC", "ETH", "BNB"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


def quote_asset(symbol: str) -> str:
    """Derive the quote asset from a symbol such as ``BTCUSDT`` → ``USDT``."""
    for quote in ("USDT", "BUSD", "USDC", "BTC", "ETH", "BNB"):
        if symbol.endswith(quote):
            return quote
    return ""
