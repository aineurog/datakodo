# DataKodo Usage Guide — MetaTrader 5

DataKodo is a unified market data library: one interface, many providers.
This page covers the **MetaTrader 5** adapter, which reads from a local MT5
terminal. The Binance adapter (spot / USD-M futures) is documented separately
in [docs/binance.md](binance.md).

Other providers (Alpaca, Polygon, IBKR, ...) will get their own pages
following the same structure.

## Data Precision

Values are returned **unrounded**, exactly as the terminal reports them
(design doc sec 2/18: normalization is structural, not value-level). The
canonical schema maps columns and shapes; price/volume magnitudes pass
through untouched so nothing is fabricated or lost. MT5's precision metadata
(`digits`, `point`, and `tick_size`) is exposed on the `Instrument` object, so
you can format to your own spec without the library deciding for you.

---

# MT5 Adapter

The MetaTrader 5 (MT5) adapter reads market data from a **local MT5 terminal**:
forex pairs, CFDs, metals, indices, equities, and futures. The terminal has no
HTTP API, so DataKodo uses the MT5 Python package, which talks to the running
terminal over an inter-process bridge.

- Historical OHLCV in every canonical timeframe, returned in true **UTC**.
- Fundamentals / reference data from `symbol_info` plus the latest tick.
- Instrument classification (spot vs futures, forex, CFD, metal, ...).
- **No API key.** MT5 authenticates through the terminal login
  (login/password/server) or the account already logged into the terminal.
- **Windows only.** The `MetaTrader5` package requires a Windows MT5 terminal.

## Prerequisites

- MetaTrader 5 **installed and running**, logged in to a broker account.
- Symbols you fetch should be visible in MarketWatch; the adapter selects each
  symbol automatically, which also kicks off its history download.

## Installation

```bash
pip install datakodo[mt5]
```

## Quick Start

```python
from datetime import UTC, datetime

from datakodo import Client

start = datetime(2026, 8, 1, tzinfo=UTC)
end = datetime.now(UTC)

with Client("mt5") as client:
    df = client.fetch_ohlcv("EURUSD", "1h", start, end)

print(df.head())
```

`with Client("mt5")` connects to the terminal on entry and disconnects on exit.
The result is a `pandas.DataFrame` with the canonical base columns
`timestamp, open, high, low, close, volume, is_closed`.

## Configuration

Provider settings live on `MT5Config` and read the `MT5_` environment prefix
(e.g. `MT5_LOGIN`):

| Setting | Default | Description |
| --- | --- | --- |
| `terminal_path` | `C:\Program Files\MetaTrader 5` | Terminal install folder or `terminal64.exe` path. |
| `login` | `None` | Account login (int). `None` uses the terminal's default account. |
| `password` / `server` | `""` | Account password / broker server. |
| `timeout` | `10.0` | Timeout (s) for terminal operations. |
| `rate_limit_rate` | `5.0` | Token-bucket refill rate (tokens/sec). |
| `rate_limit_burst` | `10` | Token-bucket burst capacity. |
| `max_bars` | `1000` | Bars per request; wider ranges are auto-paginated. |
| `market_type` | `"forex"` | Default market classification. |

Example `.env`:

```bash
MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5
MT5_LOGIN=12345678
MT5_PASSWORD=...
MT5_SERVER=MyBroker-Demo
```

Or pass settings explicitly, which take precedence over the environment:

```python
from datetime import UTC, datetime

from datakodo import Client
from datakodo.adapters.mt5.config import MT5Config

mt5_config = MT5Config(login=12345678, password="...", server="MyBroker-Demo")
with Client("mt5", mt5_config=mt5_config) as client:
    df = client.fetch_ohlcv("EURUSD", "4h", datetime(2026, 8, 1, tzinfo=UTC), datetime.now(UTC))
```

## Timeframes

MT5 offers every canonical timeframe natively, so resampling never triggers:

```text
1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1mo
```

## Fetching OHLCV

Only fully **closed** candles are returned by default; the still-forming bar
is excluded before validation. Set `include_live=True` to keep it, marked
`is_closed=False`.

```python
df = client.fetch_ohlcv("XAUUSD", "4h", start, end, include_live=False)
```

Timestamps are bar **open** times in true UTC. MT5 keeps history that is loaded
into the terminal; a range with no loaded bars raises `DataNotAvailableError`,
and a symbol unknown to the terminal raises `SymbolNotFoundError`. Whenever the
returned candles skip an interval, a **gap-detection warning** is logged with
the number of missing candles.

### Selecting columns

By default you get the invariant base columns. MT5 also offers opt-in extras,
requested with `columns`:

```python
df = client.fetch_ohlcv("EURUSD", "1h", start, end, columns="all")
df = client.fetch_ohlcv("EURUSD", "1h", start, end, columns=["spread", "real_volume"])
```

`columns` accepts `"basic"` (default), `"all"`, or an explicit list. MT5's
extras are `session` (forex only), `spread`, and `real_volume`.

## Fundamentals / Reference Data

`fetch_fundamentals()` returns a canonical `Fundamentals` record combining
`symbol_info` (currencies, description, classification) with the latest tick
time, converted to true UTC:

```python
with Client("mt5") as client:
    f = client.fetch_fundamentals("EURUSD")
print(f.name, f.currency, f.as_of)
```

## Instrument Classification

`instrument()` classifies a symbol from its MT5 metadata (`symbol_info`):
futures, forex, CFD, metal, equity, crypto, and more. An optional `market_type`
hint (`"spot"` / `"futures"` / ...) is validated against the detected
classification and raises `ProviderError` on a mismatch.

```python
with Client("mt5") as client:
    inst = client.instrument("EURUSD", market_type="spot")
print(inst.asset_class, inst.instrument_type)
```

## Batch / Multi-Symbol Fetching

`fetch_ohlcv_batch()` fetches the same timeframe and date range for several
symbols in one call — and returns `{symbol: frame}` (or one combined frame with
a `symbol` column when `combine=True`):

```python
with Client("mt5") as client:
    result = client.fetch_ohlcv_batch(["EURUSD", "GBPUSD", "XAUUSD"], "1h", start, end)
# result: {"EURUSD": DataFrame, "GBPUSD": DataFrame, "XAUUSD": DataFrame}
```

How it runs (design doc sec 11): adapters declare a `concurrency_model`, and
batch concurrency is gated on it. Thread-safe adapters (Binance, Polygon,
Alpaca) fan out over a `ThreadPoolExecutor`; **MT5 is `"serial"`** — the MT5
Python API is a COM-style bridge with a single connection to one terminal, and
concurrent calls through it are not safe. So for MT5 the batch is a plain
sequential loop: each symbol's `fetch_ohlcv` runs one at a time through the
same terminal connection:

```text
fetch_ohlcv("EURUSD", "1h", ...)
fetch_ohlcv("GBPUSD", "1h", ...)
fetch_ohlcv("XAUUSD", "1h", ...)
```

Consequences:

- `max_workers` is **ignored** for MT5 — there is no thread pool to size.
- Every symbol's request still passes through the terminal's token-bucket rate
  limiter, so the configured MT5 rate limit is respected regardless.
- The call stays synchronous; no asyncio knowledge needed.
- A symbol that fails (unknown symbol, no bars in range, retry budget
  exhausted) raises immediately and aborts the remaining symbols — the same
  behavior Binance has when a thread raises.

## Rate Limiting and Retry

Requests are gated by a token bucket (`MT5Config.rate_limit_rate` /
`rate_limit_burst`). A full bucket backs off and retries with exponential
delay governed by the global `Config.max_retries` / `Config.retry_base_delay`;
when the retry budget is exhausted a `RetriesExhaustedError` is raised.

## Streaming

MT5 has no tick-push feed through the Python API, so streaming is **not
supported** — `stream_trades` / `stream_orderbook` are not available. Use
`fetch_ohlcv` and `fetch_fundamentals`, which are blocking polls.

## Instrument Search

MT5 has no cheap symbol-list endpoint, so `search_instruments()` is not
supported and raises `NotSupportedError`.