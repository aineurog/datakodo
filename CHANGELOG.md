# Changelog

All notable changes to DataKodo are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — Unreleased

### Added
- Core canonical schemas: OHLCV, Trade, OrderBook (Pydantic v2, schema version 1.0).
- Structured Instrument model with typed asset class extensions.
- Abstract AdapterInterface and StorageBackend contracts.
- Standardized exception hierarchy containing authentication, rate limit,
  symbol not found, and other provider mapped errors.
- Config system via pydantic-settings with .env support.
- Adapter scaffolding for Binance, Alpaca, Polygon, MT5, and IBKR.
- Each adapter subpackage includes rest, websocket, and mapper modules.
- Token bucket rate limiter per provider instance.
- Timeframe mapping from canonical enums to provider specific strings.
- Streaming base utilities: automatic reconnect with exponential backoff
  and async generator merging.
- Order book maintainer for snapshot + delta L2 book tracking.
- Timeframe resampling with standard OHLCV aggregation rules.
- Auto pagination and stitching across large date ranges.
- Data quality validation: non negative prices, high >= low, monotonic
  timestamps, duplicate detection.
- Corporate actions: split and dividend price adjustments.
- Parquet storage backend implementing the StorageBackend interface.
- Cache key generation and invalidation rules.
- Provider extras in pyproject.toml for all five Phase 1 adapters.
- CI workflow: ruff lint, mypy type check, pytest with coverage on every push.
- Gated integration test workflow for live API tests.
