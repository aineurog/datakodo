"""Binance adapter - step-by-step execution demo.

Runs every public adapter function with all its parameters, for both the spot
and USD-M futures markets, passing each return value into the next step in the
actual application order. Prints ``Calling <fn>`` before each call and
``Returned: <result>`` immediately after, with no assertions.

Run:
    python tests/adapters/test_binance.py
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pandas as pd

from datakodo.adapters.binance.adapter import BinanceAdapter
from datakodo.adapters.binance.mapper import map_ohlcv, map_trades
from datakodo.adapters.binance.rest import BinanceREST
from datakodo.adapters.binance.ws import BinanceWS
from datakodo.ops.validation import validate_ohlcv
from datakodo.storage.cache import build_cache_key
from datakodo.storage.parquet import ParquetBackend

SYMBOL = "BTCUSDT"
INTERVAL = "1h"
D1 = "1d"
START = datetime(2026, 8, 2, tzinfo=UTC)
END = datetime(2026, 8, 3, tzinfo=UTC)
MARKETS = ["spot", "futures"]
OUT_DIR = Path("demo_output")
OUT_DIR.mkdir(exist_ok=True)


def _print_len(rows: list) -> None:
    print(f"Returned: {len(rows)} candles, e.g. {rows[0] if rows else []}")


def _save_csv(df: pd.DataFrame, name: str) -> None:
    path = OUT_DIR / f"{name}.csv"
    df.to_csv(path, index=False)
    print(f"Saved: {path}")


def main() -> None:
    # 1. Adapter + instruments for both markets
    print("Calling BinanceAdapter()")
    adapter = BinanceAdapter()
    print(f"Returned: {adapter}")

    for market in MARKETS:
        print(f"Calling adapter.instrument('{SYMBOL}', market_type='{market}')")
        inst = adapter.instrument(SYMBOL, market_type=market)
        print(f"Returned: {inst}")

    # 2. REST klines: both markets, with all params
    print("Calling BinanceREST()")
    rest = BinanceREST()
    print(f"Returned: {rest}")

    for market in MARKETS:
        print(f"Calling rest.klines('{SYMBOL}', '{INTERVAL}', start, end, market_type='{market}')")
        raw = rest.klines(SYMBOL, INTERVAL, START, END, market_type=market)
        _print_len(raw)

    print("Calling rest.klines(symbol, interval, start, end, limit=5, market_type='spot')")
    raw_spot_5 = rest.klines(SYMBOL, INTERVAL, START, END, limit=5, market_type="spot")
    _print_len(raw_spot_5)

    print("Calling rest.klines(symbol, interval, start, end, limit=5, market_type='futures')")
    raw_fut_5 = rest.klines(SYMBOL, INTERVAL, START, END, limit=5, market_type="futures")
    _print_len(raw_fut_5)

    # 3. Mapper + validation chain
    for market in MARKETS:
        print(f"Calling map_ohlcv(rest.klines(..., market_type='{market}'))")
        raw = rest.klines(SYMBOL, INTERVAL, START, END, market_type=market)
        df = map_ohlcv(raw)
        print(
            f"Returned: DataFrame {len(df)} rows x {df.shape[1]} cols, columns={list(df.columns)}"
        )

        print(f"Calling validate_ohlcv(map_ohlcv(...)) for {market}")
        validate_ohlcv(df)
        print("Returned: None (validation passed)")

    # 4. Cache key + storage for both markets
    for market in MARKETS:
        prefix = f"binance-{market}"
        key = build_cache_key(prefix, SYMBOL, INTERVAL, (START.isoformat(), END.isoformat()))
        print(f"Calling build_cache_key('{prefix}', symbol, interval, range)")
        print(f"Returned: {key}")

        print("Calling ParquetBackend()")
        store = ParquetBackend()
        print(f"Returned: {store}")

        print(f"Calling store.write('{key}', df)")
        store.write(key, df)
        print("Returned: None")

        print(f"Calling store.read('{key}')")
        out = pd.DataFrame(store.read(key))
        print(f"Returned: {len(out)} candles x {list(out.columns)}")

        print(f"Calling store.exists('{key}')")
        print(f"Returned: {store.exists(key)}")

    # 5. map_trades (buy and sell)
    print("Calling map_trades({'T','p','q','m':True})")
    buy = map_trades({"T": 1704067200000, "p": "50000.0", "q": "0.1", "m": True})
    print(f"Returned: {buy}")

    print("Calling map_trades({'m':False})")
    sell = map_trades({"T": 1704067200000, "p": "50001.0", "q": "0.2", "m": False})
    print(f"Returned: {sell}")

    # 6. fetch_ohlcv end-to-end for both markets (validates + persists internally)
    for market in MARKETS:
        msg = f"Calling fetch_ohlcv(symbol, interval, start, end, market_type='{market}')"
        print(msg)
        fetched = adapter.fetch_ohlcv(SYMBOL, INTERVAL, START, END, market_type=market)
        print(f"Returned: {len(fetched)} candles x {list(fetched.columns)}")
        _save_csv(fetched, f"ohlcv_{INTERVAL}_{market}")

    print("Calling fetch_ohlcv(daily, market_type='spot')")
    daily = adapter.fetch_ohlcv(SYMBOL, D1, START, END, market_type="spot")
    print(f"Returned: {len(daily)} daily candles")
    _save_csv(daily, f"ohlcv_{D1}_spot")

    print("Calling fetch_ohlcv(large range, 1h, market='spot', persist=False)")
    big = adapter.fetch_ohlcv(
        SYMBOL,
        INTERVAL,
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 8, 3, tzinfo=UTC),
        persist=False,
    )
    summary = f"min={big['timestamp'].min()}, max={big['timestamp'].max()}"
    print(f"Returned: {len(big)} paginated candles ({summary})")
    _save_csv(big, "ohlcv_1h_spot_paginated")

    # 7. WebSocket streams (live, both markets, stop after a couple)
    asyncio.run(_streams())


async def _streams() -> None:
    for market in ["spot", "futures"]:
        print(f"Calling BinanceWS().trade_stream('{SYMBOL}', market_type='{market}')")
        gen = cast(AsyncGenerator, BinanceWS().trade_stream(SYMBOL, market_type=market))
        n = 0
        async for raw in gen:
            t = map_trades(raw)
            print(f"Returned: {t.timestamp} p={t.price} q={t.size} side={t.side}")
            n += 1
            if n == 2:
                break
        await gen.aclose()

    for market in ["spot", "futures"]:
        print(f"Calling BinanceWS().orderbook_stream('{SYMBOL}', market_type='{market}')")
        gen2 = cast(AsyncGenerator, BinanceWS().orderbook_stream(SYMBOL, market_type=market))
        async for raw in gen2:
            bids = raw.get("bids") or raw.get("b")
            asks = raw.get("asks") or raw.get("a")
            print(f"Returned: bids={len(bids)} asks={len(asks)}")
            break
        await gen2.aclose()


if __name__ == "__main__":
    main()
