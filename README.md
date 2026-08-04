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
- **Closed-data guarantees.** Only fully closed candles are returned and
  cached, so historical data stays consistent.
- **Built-in caching and rate limiting.** Local Parquet caching avoids
  re-fetching closed data, and requests are rate-limited automatically.

## Installation

Install DataKodo with support for your provider:

```bash
pip install datakodo[binance]
```

## Quick Start

```python
from datakodo import Config
from datakodo.adapters.binance import BinanceAdapter

config = Config()
adapter = BinanceAdapter(config=config)

df = adapter.fetch_ohlcv("BTCUSDT", "1h", start, end)
print(df)
```

Public market data needs no API key. See the
[usage guide](docs/usage.md) for configuration, streaming, and more examples.

## Supported Providers

| Provider | Markets | Status |
| --- | --- | --- |
| Binance | Spot, USD-M perpetual futures | Implemented |
| Alpaca, Bybit, and others | — | Planned |

## Documentation

- [Usage guide](docs/usage.md) — installation, configuration, and examples.
- [Design document](docs/design-document.md) — architecture and design decisions.

## License

Distributed under the [MIT License](LICENSE).
