# DataKodo

Unified market data adapter library for accessing financial exchange data
through a common interface.

<!-- Badges: placeholders — update the URLs once the package is published and CI is public. -->
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![PyPI version](https://img.shields.io/pypi/v/datakodo.svg)](https://pypi.org/project/datakodo/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Build status](https://img.shields.io/badge/build-passing-brightgreen.svg)](#)

DataKodo gives you a single, consistent way to work with market data from
different exchanges. Instead of learning each exchange's API, you use one
interface and let DataKodo handle the details — the same code works whether
your data comes from Binance today or another provider tomorrow.

## Features

- **One interface, many providers.** An adapter architecture means every
  exchange is reached through the same API, and switching providers is a
  one-line change.
- **Historical and real-time data.** Fetch OHLCV candles, trade ticks, and
  order book snapshots over REST, and stream live trades and order books
  over WebSocket. MetaTrader 5 is exposed through a synchronous (blocking)
  interface, matching its native COM-based Python API.
- **Canonical schemas.** Every provider returns data in the same shape,
  so your analysis code never depends on a specific exchange.
- **Structured instruments.** Symbols are classified into canonical
  `Instrument` objects — asset class (forex, metal, CFD, crypto, future),
  instrument type (spot/futures), exchange, currency, and asset-class
  extensions.
- **Closed-data guarantees.** Only fully closed candles are returned and
  cached, so historical data stays consistent.
- **Built-in caching and rate limiting.** Local Parquet caching avoids
  re-fetching closed data, and requests are rate-limited automatically.

## Installation

Install DataKodo with support for your provider:

```bash
pip install datakodo[binance]   # Binance (spot + USD-M futures)
pip install datakodo[mt5]       # MetaTrader 5 (Windows only)
```

## Quick Start

### Binance

```python
from datakodo import Config
from datakodo.adapters.binance import BinanceAdapter

config = Config()
adapter = BinanceAdapter(config=config)

df = adapter.fetch_ohlcv("BTCUSDT", "1h", start, end)
print(df)
```

Public market data needs no API key.

### MetaTrader 5

MT5 requires a running, logged-in terminal on Windows. No API keys — DataKodo
attaches to the terminal account:

```python
from datakodo.adapters.mt5 import MT5Adapter

with MT5Adapter() as adapter:
    df = adapter.fetch_ohlcv("EURUSD", "1h", start, end)
print(df)
```

MT5 symbols are self-describing: `XAUUSD` is spot gold, while a gold future is
a separate contract symbol (e.g. `GCZ24`). Use `instrument()` to classify any
symbol.

## Supported Providers

| Provider | Markets | Status |
| --- | --- | --- |
| Binance | Spot, USD-M perpetual futures | Implemented |
| MetaTrader 5 | Forex, CFDs, metals, crypto, futures (where listed) | Implemented |
| Alpaca, Bybit, and others | — | Planned |

## Documentation

- [Binance adapter](docs/binance.md) — installation, configuration, and examples.
- [MT5 adapter](docs/mt5.md) — installation, configuration, and examples.
- [Design document](docs/design-document.md) — architecture and design decisions.

## License

Distributed under the [MIT License](LICENSE).
