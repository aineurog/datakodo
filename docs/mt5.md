# MT5 Adapter

The MT5 adapter provides access to **MetaTrader 5** market data — forex,
CFDs, metals, crypto, and (where the broker lists them) exchange-traded
futures — through the canonical DataKodo API.


## Installation

Install DataKodo with the MT5 extra (Windows only):

```bash
pip install datakodo[mt5]
```

## Quick Start

Fetch hourly candles for EURUSD:

```python
from datetime import UTC, datetime

from datakodo.adapters.mt5 import MT5Adapter

with MT5Adapter() as adapter:
    df = adapter.fetch_ohlcv(
        "EURUSD",
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
from datakodo.adapters.mt5 import MT5Adapter

config = Config()
adapter = MT5Adapter(config=config)
```

`Config` reads its values from environment variables (prefixed with
`DATAKODO_`) and an optional `.env` file in the project root, so in most cases
`Config()` works with no arguments:

```bash
# .env
DATAKODO_MT5_LOGIN=12345678
DATAKODO_MT5_PASSWORD=my_password
DATAKODO_MT5_SERVER=FusionMarkets-Demo
DATAKODO_MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5
```

If you leave `login`/`password`/`server` empty, the adapter attaches to the
terminal's default (last-used) account.

Common MT5 settings:

| Setting | Default | Description |
| --- | --- | --- |
| `mt5_login` | `None` | Account login. `None` uses the terminal's default account. |
| `mt5_password` | `""` | Account password. |
| `mt5_server` | `""` | Broker server name (e.g. `FusionMarkets-Demo`). |
| `mt5_terminal_path` | `C:\Program Files\MetaTrader 5` | Install folder or `terminal64.exe` path. Empty uses the default install. |
| `mt5_rate_limit_rate` | `5.0` | Token-bucket refill rate (tokens/sec) around data requests. |
| `mt5_rate_limit_burst` | `10` | Token-bucket burst capacity. |
| `flag_resample` | `True` | Log a warning (instead of an info message) when a non-native timeframe is derived by resampling. |

You can also pass the terminal path directly to the adapter instead of config:

```python
from datakodo.adapters.mt5 import MT5Adapter

adapter = MT5Adapter(terminal_path=r"C:\Program Files\MetaTrader 5")
```

## Adapter Lifecycle

MT5 holds a live terminal connection, so always use the context manager — it
runs `connect()` on entry and `disconnect()` on exit:

```python
from datakodo.adapters.mt5 import MT5Adapter

with MT5Adapter() as adapter:
    df = adapter.fetch_ohlcv("EURUSD", "1h", start, end)
# connection closed here
```

`adapter.connect()`, `adapter.disconnect()`, and `adapter.close()` are also
available explicitly.

> **Important:** all terminal work must happen inside the `with` block. The
> connection is shut down when it exits, so calls made after it will raise
> `ConnectionError`.

## Symbols, Spot vs Futures

MT5 symbols are **self-describing** — the symbol *is* the contract, so you do
not pass a spot/futures flag to fetch data:

- Spot gold is `XAUUSD`; a gold **future** is a separate contract symbol such
  as `GCZ24`.
- Want spot data? Use the spot symbol. Want futures data? Use the futures
  symbol.

`instrument()` tells you what a symbol actually is by reading the broker's
`SymbolInfo` metadata (contract cost/margin mode and the Market Watch tree):

```python
from datakodo.adapters.mt5 import MT5Adapter

with MT5Adapter() as adapter:
    spot = adapter.instrument("XAUUSD")   # -> metal/spot
    fut  = adapter.instrument("GCZ24")    # -> future/futures (if listed)
```

`instrument()` returns a canonical `Instrument` object: base fields (`symbol`,
`asset_class`, `instrument_type`, `exchange`, `currency`, `provider_symbol`)
plus asset-class extensions (`ForexExtension` with pip/lot sizing,
`FutureExtension` with expiry/contract/tick/multiplier).

### The optional `market_type` hint

`instrument()` accepts `market_type="spot"` or `"futures"` as a **validation
hint only**. It does not choose the market; it just confirms the symbol matches
your expectation and raises `ProviderError` on mismatch:

```python
with MT5Adapter() as adapter:
    adapter.instrument("XAUUSD", market_type="spot")      # ok -> metal/spot
    adapter.instrument("XAUUSD", market_type="futures")   # raises: XAUUSD is spot
```

### How classification works

MT5's `trade_calc_mode` reports how the broker prices each instrument (forex,
futures, CFD, ...). The numeric values of these `SYMBOL_CALC_MODE_*` constants
**vary by package build** (e.g. this build reports `EXCH_FUTURES=33` where the
standard docs say `6`), so DataKodo resolves them from the installed module at
runtime rather than hardcoding integers. The Market Watch `path` (e.g.
`Forex\EURUSD`, `Futures\...`) is used as a fallback for classification.

## Timeframes

The same canonical timeframes are used everywhere:

```text
1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1mo
```

## Resampling

MT5 offers every canonical timeframe natively, so all requests hit the
terminal directly. The resampling mechanism (design doc sec 7) still applies:
if a requested timeframe were outside an adapter's `native_timeframes`, the
nearest smaller native timeframe is fetched and resampled up locally using
standard OHLCV aggregation (`open` = first, `high` = max, `low` = min,
`close` = last, `volume` = sum). Resampling is upsampling only.

By default a warning is logged when a non-native timeframe is derived by
resampling. Set `flag_resample=False` on `Config` to log it quietly instead.

## Fetching OHLCV

`fetch_ohlcv()` returns fully **closed** candles only: the still-forming (open)
bar is excluded before the data is validated and gap-checked. Set
`include_live=True` to also return the open bar.

```python
from datetime import UTC, datetime

from datakodo.adapters.mt5 import MT5Adapter

with MT5Adapter() as adapter:
    df = adapter.fetch_ohlcv(
        "EURUSD",
        "1h",
        start=datetime(2026, 8, 4, tzinfo=UTC),
        end=datetime.now(UTC),
        include_live=False,
    )
```

Notes:

- **Symbols must be visible in Market Watch.** Call
  `adapter._terminal.symbol_select("EURUSD", True)` before fetching a symbol
  that is not currently loaded, otherwise the terminal may return no history.
  (A dedicated adapter-level helper is planned.)
- **History availability.** MT5 only returns bars the terminal has actually
  downloaded. If a symbol's chart was never opened — or "Max. bars in chart"
  is set low — an empty frame is returned and a warning is logged. Open the
  chart in MT5 (or raise the limit) and retry.
- **Large ranges are paginated** automatically into multiple terminal requests
  and stitched together (design doc sec 11).
- **Timezones.** All timestamps are returned in true UTC (the adapter converts
  MT5's server time using the broker's offset) — design doc sec 9.
- **Gaps.** Missing candles (e.g. forex weekends) are detected and logged as
  warnings.

## Batch / Multi-Symbol Fetching

`fetch_ohlcv_batch()` fetches the same timeframe and date range for several
symbols in a single call. Fetches run concurrently on a thread pool and still
funnel through the provider's rate limiter; the call itself stays synchronous.

```python
from datetime import UTC, datetime

from datakodo.adapters.mt5 import MT5Adapter

with MT5Adapter() as adapter:
    result = adapter.fetch_ohlcv_batch(
        ["EURUSD", "XAUUSD"],
        "1h",
        start=datetime(2026, 8, 4, tzinfo=UTC),
        end=datetime.now(UTC),
    )
# result: {"EURUSD": DataFrame, "XAUUSD": DataFrame}
```

Pass `combine=True` to get a single DataFrame with a `symbol` column instead of
a mapping. `max_workers` controls the thread pool size. Any additional keyword
argument — `include_live`, `output_format`, `persist`, ... — is forwarded to
each `fetch_ohlcv` call.

## Output Format

`fetch_ohlcv()` (and `fetch_ohlcv_batch()`) return **pandas DataFrames** by
default. You can request `polars`, `arrow`, or `numpy` instead, either globally
on `Config` or per call:

```python
from datakodo import Config

config = Config(output_format="polars")          # global
df = adapter.fetch_ohlcv("EURUSD", "1h", start, end, output_format="arrow")  # per call
```

Supported values: `pandas` (default), `polars`, `arrow`, `numpy`. An unsupported
value raises `ValueError`.

## Fundamentals / Reference Data

`fetch_fundamentals()` returns a canonical `Fundamentals` record combining the
symbol's reference data (currencies, description, contract sizing) from
`SymbolInfo` with its latest live price from the current `Tick`:

```python
from datakodo.adapters.mt5 import MT5Adapter

with MT5Adapter() as adapter:
    f = adapter.fetch_fundamentals("EURUSD")
    print(f.symbol, f.asset_class, f.instrument_type)   # EURUSD forex spot
    print(f.currency, f.exchange, f.latest_price)       # USD MetaTrader 5 1.15233
```

The classification (asset class / instrument type / currency) is derived by
the same `map_instrument` logic as `instrument()`, so the two always agree.

> **Note:** MT5 does not expose a 24h OHLCV-ticker endpoint, so fields like
> `high_24h`/`volume_24h` are left `None` rather than fabricated. On some
> brokers the tick's `last` price reads `0.0` outside trading hours; the
> adapter falls back to `bid` when `last` is unavailable.

## Caching

Closed historical data is cached locally (Parquet) and treated as immutable —
it is only re-fetched on an explicit refresh. The still-forming candle is
never cached.

```python
from datakodo import Config
from datakodo.adapters.mt5 import MT5Adapter

config = Config(cache_enabled=True, cache_dir="datakodo_cache")
with MT5Adapter(config=config) as adapter:
    df = adapter.fetch_ohlcv("EURUSD", "1h", start, end)
```

`fetch_ohlcv(..., persist=False)` disables writing to cache for that call;
`force_refresh=True` bypasses the cache read and always hits the terminal.

## Rate Limiting

Requests to the terminal are gated by a token bucket so bursts of requests
don't overwhelm the local MT5 instance. Configure on `Config`:

```python
from datakodo import Config

config = Config(mt5_rate_limit_rate=5.0, mt5_rate_limit_burst=10)
```

## Limitations

- **Windows only** — the `MetaTrader5` package is not available elsewhere.
- **Blocking only** — no `stream_trades`/`stream_orderbook`; `supports_ticks`
  and order-book streaming are `False`.
- **Terminal must be running and logged in** — DataKodo attaches to the local
  terminal; it cannot start an unattended remote session.
- **No API keys** — authentication comes from the terminal account.
