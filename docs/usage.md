# DataKodo Usage Guide

## Installation

```bash
pip install datakodo
```

With provider extras:

```bash
pip install datakodo[binance,alpaca]
pip install datakodo[all]
```

## Quick Start

```python
from datakodo import Config
from datakodo.adapters.binance import BinanceAdapter

config = Config(output_format="pandas")
adapter = BinanceAdapter()

df = adapter.fetch_ohlcv("BTCUSDT", "1h", start, end)
```

## Configuration

DataKodo uses `pydantic-settings` loaded from environment variables
(prefixed with `DATAKODO_`) and a `.env` file:

```bash
export DATAKODO_OUTPUT_FORMAT=polars
export DATAKODO_CACHE_DIR=/data/datakodo_cache
```

## Output Format

The default output format is pandas DataFrame. Change it globally or
per call:

```python
Config(output_format="polars")
```

Supported formats: `pandas`, `polars`, `arrow`, `numpy`.

## Caching

Enable local caching to avoid re-fetching closed historical data:

```python
Config(cache_enabled=True, cache_dir="datakodo_cache")
```

## Rate Limiting

Rate limiting is handled automatically. Configure retry behavior:

```python
Config(max_retries=5, retry_base_delay=2.0)
```
