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
  over WebSocket.
- **Canonical schemas.** Every provider returns data in the same shape,
  so your analysis code never depends on a specific exchange.
- **Closed-data guarantees.** Only fully closed candles are returned, so
  historical data stays consistent.
- **Built-in rate limiting.** Requests are rate-limited automatically, with
  retry and exponential backoff.
- **Flexible output.** Return data as pandas (default), polars, or Arrow.

## Installation

Install DataKodo with support for your provider:

```bash
pip install datakodo[binance]    # Binance spot + USD-M futures
pip install datakodo[mt5]        # MetaTrader 5 terminal (Windows)
```

## Quick Start

```python
from datetime import UTC, datetime

from datakodo import Client

client = Client("binance")
df = client.fetch_ohlcv(
    "BTCUSDT",
    "1h",
    start=datetime(2026, 8, 1, tzinfo=UTC),
    end=datetime.now(UTC),
)
print(df)
```

The same `Client` facade works for MetaTrader 5 (requires a running terminal):

```python
from datetime import UTC, datetime

from datakodo import Client

with Client("mt5") as client:
    df = client.fetch_ohlcv(
        "EURUSD",
        "1h",
        start=datetime(2026, 8, 1, tzinfo=UTC),
        end=datetime.now(UTC),
    )
print(df)
```

Public market data needs no API key. Provider usage guides:
- [Binance](docs/binance.md)
- [MetaTrader 5](docs/mt5.md)

## Supported Providers

| Provider | Markets | Status |
| --- | --- | --- |
| Binance | Spot, USD-M perpetual futures | Implemented |
| MetaTrader 5 | Forex, CFDs, metals, indices, futures (local Windows terminal) | Implemented |
| Alpaca, Bybit, and others | — | Planned |

## Documentation

- [Usage guide — Binance](docs/binance.md) — installation, configuration, and examples.
- [Usage guide — MetaTrader 5](docs/mt5.md) — installation, configuration, and examples.

## License

Distributed under the [MIT License](LICENSE).
