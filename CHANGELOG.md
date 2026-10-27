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
- Session-aware timeframe resampling with an explicit source timeframe,
  session-anchored buckets, period anchors at the first session open, DST
  handling, and aggregation of optional columns (`spread` max, volume-weighted
  `vwap`, summed additive extras).
- `SessionProfile` for recurring trading windows, including previous-day
  openings (forex rollover, futures) and maintenance breaks.
- `SessionWindow` allows multiple windows per day with explicit start/end day
  offsets, so overnight sessions, maintenance breaks, and 17:00 rollovers are
  expressible. Intraday anchors (`utc_midnight`, `local_midnight`,
  `session_open`) and a trading-date rule (`open_date` vs `close_date`) select
  how bars are labelled.
- Built-in session profiles `CRYPTO_UTC`, `FX_NY17`, `METALS_NY17`,
  `XNYS_REGULAR`, and `CME_GLOBEX`.
- Optional `datakodo[calendar]` extra backed by `exchange_calendars` for real
  exchange holidays, half days, and DST-aware session opens and closes; without
  it `resolve_calendar` falls back to the built-in calendars.
- `SessionCalendar` sizes expected bars from session minutes, so gap detection
  and resampling understand daily breaks and 17:00 trading-day boundaries.
- Canonical `2h`, `8h`, and `12h` timeframes.
- Early close overrides on `ExchangeCalendar`, applied when sizing resampled
  sessions.
- Resampled bars report `source_count`, `expected_count`, `is_complete`,
  `is_partial`, and `close_timestamp`; `incomplete="keep"|"drop"|"raise"`
  controls how bars that are not fully closed are handled.
- Auto pagination and stitching across large date ranges.
- Batch / multi-symbol OHLCV fetching (`fetch_ohlcv_batch`).
- Half-open fetch contract: `[start, end)` in UTC, closed bars by default,
  accurate `is_closed`, and a request ending inside a forming bar excludes that
  bar. Period closes follow the instrument's session and calendar, so daily,
  weekly, and monthly bars are kept or dropped by their real session end
  rather than a fixed wall-clock duration.
- Period bars labelled by calendar date are moved to their session open where
  the provider allows it (`anchor_period_bars`), with the convention recorded
  in `.attrs["bar_anchor"]`.
- Instrument metadata: `volume_type` (base, quote, shares, contracts, tick,
  none), contract multiplier and point value, tick size, price precision,
  session profile and calendar, data tier, `is_cfd`, `allow_negative_prices`,
  futures expiry/roll rules, and capability flags.
- Frames carry data semantics in `.attrs` (`volume_type`, `price_adjustment`,
  `data_tier`), and MT5 distinguishes `tick_volume` from `real_volume`.
- Data quality validation: non-negative prices and volume, OHLC consistency,
  rejection of NaN/Inf, strictly increasing unique timestamps, and typed
  `DataValidationError` with the offending range. Negative prices are rejected
  unless the instrument opts in.
- Corporate actions pipeline (`apply_corporate_actions`): starts from raw
  data, applies splits before dividends, never double-adjusts, inverse-adjusts
  volume for splits, leaves dividends volume-neutral and opt-in, and returns
  `price_factor` / `volume_factor` columns so raw data is reconstructible.
- Continuous futures (`build_continuous_futures`): calendar front-contract
  selection from expiry or first-notice dates (or explicit roll dates),
  ratio/difference/no back-adjustment, a recorded roll table, and a contract
  multiplier applied only to notional metrics.
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
- Gap detection with warning logging for missing candles, using each symbol's
  resolved session profile and calendar so FX rollovers, metals, and futures
  schedules are understood and weekend closures are not flagged.
- Resampling, gap detection, and the fetch contract share the instrument's
  resolved period model instead of a hardcoded weekend calendar. Native daily
  and larger bars are relabelled at the session open before the contract runs.
- Canonical contract conformance and public `Client("mt5")` registration.
- Instrument search (`search_instruments`) over the terminal's full symbol
  universe via `symbols_get`, with a case-insensitive query and combinable
  asset-class / instrument-type / quote / exchange filters .
- Friendly select feedback: the first fetch of a symbol logs that it was added
  to the MarketWatch list and that history is downloading in the background,
  while a genuinely unknown symbol reports it was not added (it does not exist
  on the server). Terminal-side failures (e.g. `Out of memory`) surface as
  `ProviderError`, not a misleading `SymbolNotFoundError`.

### Added (Massive)
- Equity, index, and option bars resolve the XNYS calendar, forex follows the
  17:00 New York rollover, and crypto uses the 24/7 calendar. Period bars are
  anchored at the session open and the fetch contract closes them at the real
  session end.

### Added (IBKR)
- The historical path applies the shared fetch contract and validation, so its
  output will match the other adapters once the client implements the request.

### Added (Alpaca)
- US equities + crypto spot through `alpaca-py`: historical OHLCV
  (`fetch_ohlcv`, all 12 canonical timeframes native, `feed` + `adjustment`
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
- Equity bars resolve the XNYS calendar (real holidays and half days when the
  `calendar` extra is installed) and anchor daily and larger bars at the
  session open; crypto uses the 24/7 calendar and UTC-midnight periods.
- Honest non-support: no order-book depth (quotes are NBBO only) and no
  fundamentals endpoint — both raise `NotSupportedError` by design.
- Provider config (`AlpacaConfig`, `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY`
  with `ALPACA_*` aliases); paid-tier gating surfaces as
  `PaidTierRequiredError`, rate limits honor `Retry-After`.
- Usage guide: `docs/alpaca.md`.
