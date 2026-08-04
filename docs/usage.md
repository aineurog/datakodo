# DataKodo Usage Guide

DataKodo is a unified market data library: one interface, many providers.
Each provider is documented in its own top-level section below.

Currently only the **Binance** adapter is implemented and documented. When
additional providers are added, they will get their own sections (Alpaca,
Bybit, etc.) following the same structure.

---

# Binance Adapter

The Binance adapter provides access to Binance **spot** and **USD-M
perpetual futures** market data through the canonical DataKodo API:

- Historical data over REST: OHLCV candles, trade ticks, order book snapshots.
- Real-time data over WebSocket: live trade and order book streams.
- Public market data needs **no API key**.

Both markets use the same interface — pass `market_type="spot"` or
`market_type="futures"` (defaults to `config.binance_market_type`).

## Installation

Install DataKodo with the Binance extra:

```bash
pip install datakodo[binance]
```

## Quick Start

Fetch hourly candles for BTCUSDT:

```python
from datetime import UTC, datetime

from datakodo.adapters.binance import BinanceAdapter

adapter = BinanceAdapter()

df = adapter.fetch_ohlcv(
    "BTCUSDT",
    "1h",
    start=datetime(2026, 1, 1, tzinfo=UTC),
    end=datetime(2026, 1, 2, tzinfo=UTC),
)
print(df)
```

The result is a `pandas.DataFrame` with columns
`timestamp, open, high, low, close, volume, session`.

## Configuration

Create a `Config` object first, then pass it to the adapter:

```python
from datakodo import Config
from datakodo.adapters.binance import BinanceAdapter

config = Config()
adapter = BinanceAdapter(config=config)
```

`Config` holds every setting the library uses. It reads its values from
environment variables (prefixed with `DATAKODO_`) and an optional `.env`
file in the project root, so in most cases `Config()` works with no
arguments at all:

```bash
# .env
DATAKODO_BINANCE_MARKET_TYPE=spot
DATAKODO_BINANCE_TESTNET=false
```

You can also set values directly in code:

```python
from datakodo import Config
from datakodo.adapters.binance import BinanceAdapter

config = Config(binance_market_type="futures", binance_testnet=False)
adapter = BinanceAdapter(config=config)
```

Common Binance settings:

| Setting | Default | Description |
| --- | --- | --- |
| `binance_market_type` | `"spot"` | Market used when a call does not specify one: `spot` or `futures`. |
| `binance_testnet` | `False` | Use Binance's test network when `True`. |
| `binance_tld` | `"com"` | Binance domain: `com`, `us`, `jp`, ... |
| `binance_api_key` / `binance_api_secret` | `""` | Credentials. Public market data does not need them. |
| `flag_resample` | `True` | Log a warning (instead of an info message) when a non-native timeframe is derived by resampling. |

Public market data needs **no API key**, so `Config()` is all you need to
start fetching:

```python
from datakodo import Config
from datakodo.adapters.binance import BinanceAdapter

config = Config(binance_market_type="spot")
adapter = BinanceAdapter(config=config)

df = adapter.fetch_ohlcv("BTCUSDT", "1h", start, end)
```

## Timeframes

The same canonical timeframes are used everywhere:

```text
1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1mo
```

## Resampling

`fetch_ohlcv()` returns the timeframes an adapter offers **natively** by
fetching them directly from the exchange. For the **Binance** adapter all
canonical timeframes above are native, so every request hits the exchange's
KLines endpoint directly.

If a provider does **not** offer a requested timeframe natively, DataKodo
automatically fetches the nearest supported timeframe that is *smaller* than
the requested one, then **resamples it up** locally using standard OHLCV
aggregation rules (`open` = first, `high` = max, `low` = min, `close` = last,
`volume` = sum):

```text
requested 4h, provider only has 1h  -> fetch 1h, resample to 4h
requested 4h, provider only has 1m  -> fetch 1m, resample to 4h
```

Resampling is upsampling only: a timeframe smaller than the finest one the
provider offers cannot be derived, and such a request raises a `ValueError`.
Resampled output is always fully closed.

By default a warning is logged when a non-native timeframe is derived by
resampling. Set `flag_resample=False` on `Config` to log it quietly instead.

## Fetching OHLCV

`fetch_ohlcv()` returns fully **closed** candles only: the still-forming
(open) bar is excluded before the data is validated and cached. Set
`include_live=True` to also return the open bar, but it is never written
to cache.

```python
from datetime import UTC, datetime

from datakodo.adapters.binance import BinanceAdapter

adapter = BinanceAdapter()

df = adapter.fetch_ohlcv(
    "BTCUSDT",
    "1h",
    start=datetime(2026, 8, 4, tzinfo=UTC),
    end=datetime.now(UTC),
    market_type="spot",
    include_live=False,
)
```

Raises `DataNotAvailableError` when no closed bars are available for the
requested range.

## Batch / Multi-Symbol Fetching

`fetch_ohlcv_batch()` fetches the same timeframe and date range for several
symbols in a single call. Fetches run concurrently on a thread pool and still
funnel through the provider's rate limiter; the call itself stays synchronous.

```python
from datetime import UTC, datetime

from datakodo.adapters.binance import BinanceAdapter

adapter = BinanceAdapter()

result = adapter.fetch_ohlcv_batch(
    ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "1h",
    start=datetime(2026, 8, 4, tzinfo=UTC),
    end=datetime.now(UTC),
    market_type="spot",
)
# result: {"BTCUSDT": DataFrame, "ETHUSDT": DataFrame, "SOLUSDT": DataFrame}
```

Pass `combine=True` to get a single DataFrame with a `symbol` column instead of
a mapping. `max_workers` controls the thread pool size (defaults to
`min(len(symbols), 8)`). Any additional keyword argument — `market_type`,
`include_live`, `output_format`, ... — is forwarded to each `fetch_ohlcv` call.

## Output Format

`fetch_ohlcv()` (and `fetch_ohlcv_batch()`) return **pandas DataFrames** by
default. You can request `polars`, `arrow`, or `numpy` instead, either globally
on `Config` or per call:

```python
from datakodo import Config

config = Config(output_format="polars")          # global
df = adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, output_format="arrow")  # per call
```

Supported values: `pandas` (default), `polars`, `arrow`, `numpy`. An unsupported
value raises `ValueError`.

## Fundamentals / Reference Data

`fetch_fundamentals()` returns a canonical `Fundamentals` record combining
live price/volume stats (from the Binance 24h ticker) with reference data
(base/quote asset, trading status, permissions) from exchange info:

```python
from datakodo.adapters.binance import BinanceAdapter

adapter = BinanceAdapter()
f = adapter.fetch_fundamentals("BTCUSDT", market_type="spot")
print(f.latest_price, f.currency)          # 63758.0 USDT
print(f.crypto.status, f.crypto.base_asset)  # TRADING BTC
```

### Understanding `crypto.status`

`f.crypto.status` is the symbol's **trading status on the exchange** (from
Binance exchange info), not a DataKodo value. Check it before acting on data —
a symbol that isn't `TRADING` can't be traded normally.

Spot values:

| Value | Meaning |
| --- | --- |
| `TRADING` | Active — orders allowed. |
| `BREAK` | Trading paused temporarily (e.g. maintenance). |
| `HALT` | Trading halted (under review / delisting soon). |

USD-M futures use additional values such as `PENDING_TRADING` (listing not yet
live) and `CLOSE_DELIVERY` (expiry settlement window).

## Client Facade

`Client` is the provider-agnostic front door — the same code works for any
registered provider. You can also register new providers without touching core:

```python
from datakodo import Client, Config

client = Client("binance", config=Config(binance_market_type="spot"))
df = client.fetch_ohlcv("BTCUSDT", "1h", start, end)
client.fetch_fundamentals("BTCUSDT")
```

Unregistered providers raise `ValueError` listing the available ones.

## Adapter Lifecycle

Adapters support the context manager protocol (design doc sec 23): `connect()`
runs on entry, `disconnect()` on exit. The Binance defaults are no-ops, but the
contract is uniform across providers — MT5/IBKR will use it for their terminal
connections:

```python
from datakodo.adapters.binance import BinanceAdapter

with BinanceAdapter() as adapter:
    df = adapter.fetch_ohlcv("BTCUSDT", "1h", start, end)
```

`adapter.connect()`, `adapter.disconnect()`, and `adapter.close()` are also
available explicitly.

## Fetching Trade Data

`fetch_ticks()` returns recent or historical trade ticks as a list of
canonical `Trade` records (`timestamp`, `price`, `size`, `side`).

- Without `start`: the most recent trades, in a single call.
- With `start`: the full range is paged automatically (Binance caps each
  aggTrades request at 1000 rows and a one-hour window).

```python
from datakodo.adapters.binance import BinanceAdapter

adapter = BinanceAdapter()

recent = adapter.fetch_ticks("BTCUSDT", limit=100, market_type="spot")
print(recent[0].price, recent[0].side)
```

## Fetching the Order Book

`fetch_orderbook_snapshot()` returns a single canonical `OrderBook`
(`timestamp`, `bids`, `asks`, each level a `price`/`size` pair). The
`limit` is clamped to the depths Binance supports.

```python
from datakodo.adapters.binance import BinanceAdapter

adapter = BinanceAdapter()

book = adapter.fetch_orderbook_snapshot("BTCUSDT", limit=20, market_type="spot")
print("best bid:", book.bids[0].price, "best ask:", book.asks[0].price)
```

## Streaming (WebSocket)

Real-time streams are async generators. Each call optionally accepts
`max_messages`; when set, the stream closes its socket cleanly after that
many messages — useful for sampling.

```python
import asyncio

from datakodo.adapters.binance import BinanceAdapter

adapter = BinanceAdapter()


async def sample_trades(symbol: str, market_type: str) -> None:
    async for trade in adapter.stream_trades(symbol, market_type=market_type, max_messages=5):
        print(trade.timestamp, trade.price, trade.size, trade.side)


asyncio.run(sample_trades("BTCUSDT", "spot"))
```

Available streams:

| Stream | Yields |
| --- | --- |
| `stream_trades(symbol, market_type, max_messages)` | Canonical `Trade` records. |
| `stream_orderbook(symbol, market_type, max_messages)` | Raw Binance depth messages. |

## Caching

Closed historical data is cached locally (Parquet) and treated as immutable
— it is only re-fetched on an explicit refresh. The still-forming candle is
never cached.

```python
from datakodo import Config
from datakodo.adapters.binance import BinanceAdapter

config = Config(cache_enabled=True, cache_dir="datakodo_cache")
adapter = BinanceAdapter(config=config)
```

## Rate Limiting

Requests are gated by a token bucket so the configured Binance rate limit
is respected automatically. Retry behavior is configured on `Config`:

```python
from datakodo import Config

config = Config(max_retries=5, retry_base_delay=2.0)
```
