"""Fetch Binance data (spot + USD-M futures) and exercise config → CSV.

A single runnable script that tests both Config and fetching: builds the
core ``Config`` from env / ``.env``, shows the ``binance_*`` settings in
use, applies per-run overrides, then fetches OHLCV for both the spot and
futures markets and saves each result to a CSV under ``demo_output/``.

Run:
    python tests/adapters/test_binance_fetch.py
"""

from datetime import UTC, datetime
from pathlib import Path

from datakodo.adapters.binance.adapter import BinanceAdapter
from datakodo.core.config import Config

SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
START = datetime(2026, 8, 2, tzinfo=UTC)
END = datetime(2026, 8, 3, tzinfo=UTC)
MARKETS = ["spot", "futures"]
OUT_DIR = Path("demo_output")
OUT_DIR.mkdir(exist_ok=True)


def main() -> None:
    # 1. Config from env / .env — print what is in use.
    config = Config(_env_file=".env")
    print(f"Config binance_market_type={config.binance_market_type} tld={config.binance_tld}")

    # 2. Per-run overrides — config is overridable per call.
    config = config.model_copy(
        update={
            "binance_market_type": "spot",
            "binance_timeout": 12.0,
            "binance_rate_limit_rate": 80.0,
        }
    )
    print(
        f"Using market_type={config.binance_market_type} "
        f"timeout={config.binance_timeout} rate={config.binance_rate_limit_rate}"
    )

    adapter = BinanceAdapter(config=config)

    # 3. Fetch different data: spot + USD-M futures, save each to CSV.
    for market in MARKETS:
        print(f"Fetching {market} OHLCV for {SYMBOL} {TIMEFRAME} ...")
        df = adapter.fetch_ohlcv(
            SYMBOL, TIMEFRAME, START, END, market_type=market, persist=False
        )
        print(f"  Returned {len(df)} candles x {list(df.columns)}")

        path = OUT_DIR / f"ohlcv_{TIMEFRAME}_{market}.csv"
        df.to_csv(path, index=False)
        print(f"  Saved: {path}")

    print("Done.")


if __name__ == "__main__":
    main()
