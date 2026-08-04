"""
Simple Binance checker: connect to the real Binance exchange and save real
data to files under live_output/ so you can compare what we receive with the
actual Binance data.

Run:
    python tests/adapters/test_binance_live.py

No API key is needed (this uses public market data only).
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from datakodo.adapters.binance.adapter import BinanceAdapter
from datakodo.core.config import Config

SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
START = datetime(2026, 8, 4, tzinfo=UTC)  # fetch data from this date onward
OUT_DIR = Path("live_output")


def save(name, data):
    """Write ``data`` (dict / list / DataFrame) to a file inside live_output/."""
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / name
    if name.endswith(".csv"):
        data.to_csv(path, index=False)  # DataFrame -> CSV
    else:
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"  saved -> {path}")


async def get_trades(adapter, market_type, count=3):
    """Collect a few trades from the WebSocket stream (stops cleanly itself)."""
    trades = []
    async for trade in adapter.stream_trades(SYMBOL, market_type=market_type, max_messages=count):
        trades.append(trade.model_dump(mode="json"))
    return trades


def main():
    # 1. Configuration: build the adapter from the defaults (.env / env vars).
    config = Config()
    adapter = BinanceAdapter(config=config)
    print(f"Config: market={config.binance_market_type} tld={config.binance_tld}")

    # 2. Streamed trades over WebSocket: spot, then USD-M futures.
    print("Streaming spot trades ...")
    save("spot_trades_ws.json", asyncio.run(get_trades(adapter, "spot")))

    print("Streaming futures trades ...")
    save("futures_trades_ws.json", asyncio.run(get_trades(adapter, "futures")))

    # 3. Order book snapshots over REST: spot and futures.
    print("Fetching order books ...")
    for market in ("spot", "futures"):
        book = adapter.fetch_orderbook_snapshot(SYMBOL, limit=10, market_type=market)
        save(f"orderbook_{market}.json", book.model_dump(mode="json"))

    # 4. Most recent trade history over REST: spot and futures.
    print("Fetching recent trades ...")
    for market in ("spot", "futures"):
        trades = adapter.fetch_ticks(SYMBOL, limit=20, market_type=market)
        save(f"ticks_{market}.json", [t.model_dump(mode="json") for t in trades])

    # 5. OHLCV candles from 2026-08-04 onward, over REST: spot and futures.
    print("Fetching OHLCV ...")
    for market in ("spot", "futures"):
        df = adapter.fetch_ohlcv(SYMBOL, TIMEFRAME, START, datetime.now(UTC), market_type=market)
        save(f"ohlcv_{market}.csv", df)

    print("Done. Compare the saved files under", OUT_DIR.name, "with Binance.")


if __name__ == "__main__":
    main()
