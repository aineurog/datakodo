# DataKodo Usage Guide — Binance

DataKodo is a unified market data library: one interface, many providers.
This page covers the **Binance** adapter (spot and USD-M perpetual futures).
The MetaTrader 5 terminal adapter is documented separately in
[docs/mt5.md](mt5.md).

Additional providers (Alpaca, Polygon, IBKR, ...) will get their own
pages following the same structure.

## Data Precision

Values are returned **unrounded**, exactly as the provider reports them
(design doc sec 2/18: normalization is structural, not value-level). The
canonical schema maps columns and shapes; price/volume magnitudes pass
through untouched so nothing is fabricated or lost. Precision metadata is
exposed on the `Instrument` object where the provider reports it, so you can
format to your own spec without the library deciding for you.

---

# Binance Adapter

The Binance adapter provides access to Binance **spot** and **USD-M
perpetual futures** market data through the canonical DataKodo API:

- Historical data over REST: OHLCV candles, trade ticks, order book snapshots.
- Real-time data over WebSocket: live trade and order book streams.
- Instrument search over the full symbol list.
- Public market data needs **no API key**.

Both markets use the same interface — pass `market_type="spot"` or
`market_type="futures"` (defaults to `binance_config.market_type`).

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

The result is a `pandas.DataFrame` with the canonical base columns
`timestamp, open, high, low, close, volume, is_closed`. See
[Selecting columns](#selecting-columns) for the opt-in extra columns.

## Configuration

DataKodo splits settings into two places:

- **`BinanceConfig`** holds provider-specific settings (credentials, domain,
  default market, rate limits). It reads environment variables prefixed with
  `BINANCE_` (Binance's standard names) and an optional `.env` file.
- **`Config`** holds cross-cutting settings (output format, retries,
  resampling warnings, log level). It reads environment variables prefixed
  with `DATAKODO_` and the same `.env` file.

In most cases the defaults work and you need no arguments at all:

```python
from datakodo.adapters.binance import BinanceAdapter

adapter = BinanceAdapter()
```

To override provider settings, pass a `BinanceConfig`:

```python
from datakodo.adapters.binance import BinanceAdapter
from datakodo.adapters.binance.config import BinanceConfig

binance_config = BinanceConfig(market_type="futures", testnet=False)
adapter = BinanceAdapter(binance_config=binance_config)
```

The same values can come from the environment or a `.env` file:

```bash
# .env
BINANCE_MARKET_TYPE=spot
BINANCE_TESTNET=false
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
```

Common `BinanceConfig` settings:

| Setting | Default | Description |
| --- | --- | --- |
| `market_type` | `"spot"` | Market used when a call does not specify one: `spot` or `futures`. |
| `testnet` | `False` | Use Binance's test network when `True`. |
| `tld` | `"com"` | Binance domain: `com`, `us`, `jp`, ... |
| `api_key` / `api_secret` | `""` | Credentials. Public market data does not need them. |
| `timeout` | `10.0` | Per-request timeout in seconds. |
| `rate_limit_rate` / `rate_limit_burst` | `100.0` / `1000` | Token-bucket rate limit. |

Common cross-cutting `Config` settings:

| Setting | Default | Description |
| --- | --- | --- |
| `output_format` | `"pandas"` | Default output format: `pandas`, `polars`, or `arrow`. |
| `max_retries` | `3` | Retry attempts before giving up. |
| `retry_base_delay` | `1.0` | Base delay (seconds) for exponential backoff. |
| `flag_resample` | `True` | Log a warning (instead of an info message) when a non-native timeframe is derived by resampling. |
| `log_level` | `"INFO"` | Root logger level. |

Credentials can also be passed directly to the adapter constructor, which
takes precedence over the environment:

```python
adapter = BinanceAdapter(api_key="...", api_secret="...")
```

Public market data needs **no API key**, so `BinanceAdapter()` is all you need
to start fetching.

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
Resampling is calendar-anchored (weekly bars start on Monday, monthly bars
close on the calendar month end), and periods that span a gap in the source
data are dropped rather than silently aggregated over the gap. Resampled
output is always fully closed.

By default a warning is logged when a non-native timeframe is derived by
resampling. Set `flag_resample=False` on `Config` to log it quietly instead.

## Fetching OHLCV

`fetch_ohlcv()` returns fully **closed** candles only: the still-forming
(open) bar is excluded before the data is validated. Set `include_live=True`
to also return the open bar (marked `is_closed=False`).

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

### Selecting columns

By default you get the invariant base columns:

```text
timestamp, open, high, low, close, volume, is_closed
```

Binance also offers opt-in extra columns. Request them with the `columns`
parameter:

```python
df = adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, columns="all")
df = adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, columns=["vwap", "quote_volume"])
```

`columns` accepts `"basic"` (the default), `"all"` (every extra the provider
offers), or an explicit list. Binance's extras are `close_timestamp`,
`quote_volume`, `trades_count`, `taker_buy_base_volume`,
`taker_buy_quote_volume`, and `vwap`.

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
default. You can request `polars` or `arrow` instead, either globally on
`Config` or per call:

```python
from datakodo import Config

config = Config(output_format="polars")  # global
df = adapter.fetch_ohlcv("BTCUSDT", "1h", start, end, output_format="arrow")  # per call
```

Supported values: `pandas` (default), `polars`, `arrow`. An unsupported value
raises `ValueError`.

## Instrument Search

`search_instruments()` filters the Binance symbol list (from exchange info)
client-side:

```python
from datakodo.adapters.binance import BinanceAdapter

adapter = BinanceAdapter()

btc_pairs = adapter.search_instruments("BTC", quote="USDT", limit=20)
```

Filters are optional and combinable: a case-insensitive `query` substring,
`asset_class`, `instrument_type`, `quote` currency, and `exchange`. Each result
is a canonical `Instrument` descriptor.

## Fundamentals / Reference Data

`fetch_fundamentals()` returns a canonical `Fundamentals` record combining
live price/volume stats (from the Binance 24h ticker) with reference data
(base/quote asset, trading status, permissions) from exchange info:

```python
from datakodo.adapters.binance import BinanceAdapter

adapter = BinanceAdapter()
f = adapter.fetch_fundamentals("BTCUSDT", market_type="spot")
print(f.crypto.latest_price, f.currency)  # 63758.0 USDT
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
registered provider. Providers are discovered automatically through Python
entry points; new providers can also be registered without touching core:

```python
from datakodo import Client

client = Client("binance")
df = client.fetch_ohlcv("BTCUSDT", "1h", start, end)
client.fetch_fundamentals("BTCUSDT")
```

Unregistered providers raise `ValueError` listing the available ones. See
`Client.available_providers()` for the current list.

## Adapter Lifecycle

Adapters support the context manager protocol (design doc sec 23): `connect()`
runs on entry, `disconnect()` on exit. The Binance defaults are no-ops, but the
protocol is uniform across providers — e.g. the MT5 adapter uses it to open and
close its terminal connection:

```python
from datakodo.adapters.binance import BinanceAdapter

with BinanceAdapter() as adapter:
    df = adapter.fetch_ohlcv("BTCUSDT", "1h", start, end)
```

`adapter.connect()`, `adapter.disconnect()`, and `adapter.close()` are also
available explicitly.

## Fetching Trade Data

`fetch_ticks()` returns recent or historical trade ticks as a list of
canonical `Trade` records (`timestamp`, `price`, `size`, `side`, `trade_id`).

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
(`timestamp`, `bids`, `asks`, `last_update_id`, each level a `price`/`size`
pair). The `limit` is clamped to the depths Binance supports.

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

## Rate Limiting

Requests are gated by a token bucket so the configured Binance rate limit
is respected automatically. Retry behavior is configured on `Config`:

```python
from datakodo import Config

config = Config(max_retries=5, retry_base_delay=2.0)
```

Rate-limited and connection-failed requests are retried with exponential
backoff; when the budget is exhausted a `RetriesExhaustedError` is raised.
