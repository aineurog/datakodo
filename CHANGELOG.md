# Changelog

All notable changes to DataKodo are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — Unreleased

### Added
- Core canonical schemas: OHLCV, Trade, OrderBook (Pydantic v2, schema version 1.0).
- Canonical OHLCV base columns with an `is_closed` flag and opt-in extra columns
  selected via `columns="basic"|"all"|list`.
- Structured Instrument model with typed asset class extensions.
- Canonical Fundamentals schema (common base + asset-class block).
- Abstract AdapterInterface contract with capability flags and a concurrency
  model (`thread` vs `serial`).
- Adapter lifecycle: connect/disconnect/close + `with adapter:` support.
- Client facade (`Client(provider, config=...)`) with a provider registry and
  entry-point adapter discovery (`datakodo.adapters` group).
- `search_instruments` for filtering a provider's instrument universe.
- Standardized exception hierarchy: authentication, rate limit, symbol not
  found, timeout, retries exhausted, data validation, and provider-mapped errors.
- Config system via pydantic-settings with .env support.
- Provider-specific settings (e.g. `BinanceConfig`) decoupled from the global
  `Config`; keys come from environment variables or explicit adapter arguments.
- Configurable output format: pandas, polars, arrow.
- Adapter scaffolding for Binance, Alpaca, Massive, MT5, and IBKR.
- Each adapter subpackage includes rest, websocket, and mapper modules.
- Token bucket rate limiter per provider instance.
- Timeframe mapping from canonical enums to provider specific strings.
- Streaming base utilities: automatic reconnect with exponential backoff
  and async generator merging.
- Order book maintainer for snapshot + delta L2 book tracking.
- Gap-aware, calendar-anchored timeframe resampling with standard OHLCV
  aggregation rules.
- Auto pagination and stitching across large date ranges.
- Batch / multi-symbol OHLCV fetching (`fetch_ohlcv_batch`).
- Data quality validation: non negative prices, high >= low, monotonic
  timestamps, duplicate detection, and closed-bar marking.
- Corporate actions: split and dividend price adjustments.
- Provider extras in pyproject.toml for all five Phase 1 adapters.
- CI workflow: ruff lint, mypy type check, pytest with coverage on every push.
- Gated integration test workflow for live API tests.

### Removed
- Local caching / Parquet storage layer. Fetches always return the provider's
  latest truth; bar closure is computed from each bar's open time.

### Added (Binance)
- 24h ticker + exchange info fundamentals (`fetch_fundamentals`).
- Historical trade ticks (`fetch_ticks`), paged and deduped.
- Order book snapshot (`fetch_orderbook_snapshot`) with depth clamping.
- Instrument search (`search_instruments`) over exchange info.
- Spot + USD-M futures support with request-weight-aware rate limiting.
- Retry with exponential backoff and a `RetriesExhaustedError` on budget.

### Added (MT5)
- Historical OHLCV through a local MetaTrader 5 terminal, all canonical
  timeframes natively, timestamped in true UTC (server offset measured live).
- Full terminal lifecycle: `connect`/`disconnect` and `with Client("mt5"):`.
- Fundamentals (`fetch_fundamentals`) and instrument classification
  (`instrument`) from `symbol_info` metadata.
- Provider config (`MT5Config`, `MT5_` env prefix) with terminal credentials,
  token-bucket rate limits, and per-request bar caps.
- Retry with exponential backoff on rate-limit hits and a
  `RetriesExhaustedError` when the budget is exhausted.
- Gap detection with warning logging for missing candles.
- Canonical contract conformance and public `Client("mt5")` registration.
- Instrument search (`search_instruments`) over the terminal's full symbol
  universe via `symbols_get`, with a case-insensitive query and combinable
  asset-class / instrument-type / quote / exchange filters .
- Friendly select feedback: the first fetch of a symbol logs that it was added
  to the MarketWatch list and that history is downloading in the background,
  while a genuinely unknown symbol reports it was not added (it does not exist
  on the server). Terminal-side failures (e.g. `Out of memory`) surface as
  `ProviderError`, not a misleading `SymbolNotFoundError`.

### Added (Alpaca)
- US equities + crypto spot through `alpaca-py`: historical OHLCV
  (`fetch_ohlcv`, all 9 canonical timeframes native, `feed` + `adjustment`
  passthrough), historical ticks (`fetch_ticks`), live trade streaming
  (`stream_trades`), and instrument search (`search_instruments`) over the
  assets master.
- Historical ticks page the whole range when `start` is given; without a
  start, `limit` caps the most recent trades.
- Crypto frames omit the `session` column entirely (24/7 market, no session
  boundary); stock bars carry `session="regular"`.
- `AlpacaConfig.paper` selects the assets host so live keys work; the default
  stays on the paper host.
- Live trade streaming routes each symbol to its own queue, keeping
  concurrent symbols isolated.
- Non-native timeframes derive via the shared resample fallback
  (`pick_source_timeframe` + `resample`), flagged per `Config.flag_resample`.
- Honest non-support: no order-book depth (quotes are NBBO only) and no
  fundamentals endpoint — both raise `NotSupportedError` by design.
- Provider config (`AlpacaConfig`, `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY`
  with `ALPACA_*` aliases); paid-tier gating surfaces as
  `PaidTierRequiredError`, rate limits honor `Retry-After`.
- Usage guide: `docs/alpaca.md`.
