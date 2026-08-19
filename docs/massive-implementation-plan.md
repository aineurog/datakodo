# Massive Adapter — Implementation Plan

This plan derives the Massive adapter exclusively from
[docs/design-document.md](design-document.md). It defines what to build, which
Massive REST/WebSocket endpoints map to the common interface, what is shared,
what stays Massive-specific, the files to touch, tests, and the build order.

**Status:** plan only — no implementation beyond the rename that converted the
`polygon/` scaffolding to `massive/`.

---

## 1. What needs to be implemented

DataKodo's Phase-1 provider list (sec 24) includes Massive (formerly Polygon).
The scaffolding (`src/datakodo/adapters/massive/`) already implements the
`AdapterInterface` with capability flags and mapper primitives; the REST/WS
clients currently raise `NotImplementedError`.

Scope (per the common interface, sec 2/5):

| Common method | Sync/async | Massive backend |
| --- | --- | --- |
| `fetch_ohlcv(symbol, timeframe, start, end)` | sync | `GET /v2/aggs/ticker/{t}/{range}/{m}/{t}/{from}/{to}` (all asset classes; futures use `/futures/v1/aggs/{ticker}`) |
| `fetch_ticks(symbol, start, end)` | sync | `GET /v3/trades/{ticker}` (paged via `next_url`) |
| `fetch_orderbook_snapshot(symbol)` | sync | `GET /v2/snapshot/locale/{locale}/markets/{market}/tickers/{ticker}` |
| `fetch_fundamentals(symbol)` | sync | `GET /v3/reference/tickers/{ticker}` + `GET /stocks/v1/financials/*` |
| `instrument(symbol)` | sync | ticker prefixes (`X:`, `C:`, `I:`, `O:`) → canonical `Instrument` |
| `stream_trades(symbol)` | async gen | WS channel `T.*` / `XT.*` / `CA.…` by asset class |
| `stream_orderbook(symbol)` | async gen | WS channels `Q.*` (stocks/options), `XQ.*`/`XL2.*` (crypto) |
| `fetch_ohlcv_batch(symbols, ...)` | sync | inherited from `AdapterInterface` (thread pool + rate limiter) |

Out of scope (sec 30 + sec 26 honest capability declarations): full session
calendars, cross-provider fallback, server-side indicators (Massive `get_*`
SMA/EMA/RSI/MACD), CLI, TimescaleDB.

---

## 2. Which Massive REST endpoints are needed

Only the market-data endpoints that back the common methods:

| Endpoint | Use | Notes |
| --- | --- | --- |
| `GET /v2/aggs/ticker/{ticker}/range/{mult}/{timespan}/{from}/{to}` | OHLCV | multiplier+timespan; `adjusted`, `sort`, `limit` (max 50000); `next_url` paging |
| `GET /futures/v1/aggs/{ticker}` | futures OHLCV | `resolution` (e.g. `1min`), `window_start` (+ `.gt/.lt/...`); ns timestamps |
| `GET /v3/trades/{stockTicker}` | ticks | ns timestamps; `timestamp.gt/.gte/.lt/.lte`, `limit` (max 50000); `next_url` paging |
| `GET /v2/last/trade/{ticker}` | latest tick | efficiency for snapshots |
| `GET /v2/snapshot/locale/{locale}/markets/{market}/tickers/{ticker}` | order book snapshot | crypto book via `/book`; stocks/options snapshot |
| `GET /v3/reference/tickers/{ticker}` | instrument/fundamentals | `name`, `market`, `locale`, `currency_name`, `type`, `primary_exchange`, `market_cap`, `cik`, `active` |
| `GET /v3/reference/tickers` | symbol discovery | for `find_symbols`/validation, `market=` filter |
| `GET /v3/reference/exchanges` | exchange year mappings | for `Instrument.exchange` normalization |

Endpoints in the full Massive catalogue that DataKodo does **not** need for the
common interface: grouped aggs, open-close, previous close, news, SEC filings,
partner datasets (Benzinga/TMX/ETF Global/consumer spending), economy, options
contracts/research, snapshots beyond book/ticker, indicators. Defer those; do
not implement "because Massive provides them" (task rule 6).

---

## 3. Which Massive WebSocket streams are needed

| Common method | Massive channel | Asset classes |
| --- | --- | --- |
| `stream_trades` | `T.{sym}` (stocks/options), `XT.{pair}` (crypto), `T.{contract}` (futures) | `ev: T` / `XT` |
| `stream_orderbook` | `Q.{sym}` (stocks/options), `XQ.{pair}` / `XL2.{pair}` (crypto) | `ev: Q` / `XQ` / `XL2` |
| (optional) `stream_aggregates` | `A.{sym}` / `AM.{sym}` per-sec/per-min | not in the base interface; defer |

Auth handshake: `{"action":"auth","params":"<API_KEY>"}`; subscriptions via
`{"action":"subscribe","params":"T.AAPL,T.MSFT"}`. Messages arrive as arrays of
objects keyed by `ev`; `ev == "status"` messages are filtered.

The WebSocket tier is gated (stocks trades/aggregates need *Stocks Advanced* or
Business). Adapter must declare `requires_paid_tier` honestly and raise
`PaidTierRequiredError` (sec 22).

---

## 4. How the Massive API maps to the common/core interface

```
MassiveAdapter(AdapterInterface)
  ├── MassiveREST        # thin HTTP wrapper (urllib3/requests or `massive` client)
  │     aggs(), trades(), snapshot(), ticker_details()
  ├── MassiveWS          # async generator over WS channel messages
  │     trade_stream(), orderbook_stream()
  └── mapper.py          # raw Massive dicts -> canonical schemas
        map_ohlcv(), map_trades(), map_orderbook(), map_fundamentals()
```

- `fetch_ohlcv` returns a canonical columns DataFrame (`timestamp, open, high,
  low, close, volume, session`) like Binance/MT5. Massive agg bars map
  `t→timestamp` (UTC), `o/h/l/c/v`. Stocks include pre/post-market — DataKodo
  marks `session` per bar only where available; default `regular`.
- `timeframe` canonical (`1m,5m,1h,1d,...`) maps to `multiplier`+`timespan`
  (e.g. `1h` → `multiplier=1, timespan=hour`). Massive offers all canonical
  timeframes natively, so `native_timeframes` stays full and the resample path
  (sec 7) is available but normally unused.
- `fetch_ticks` pages `next_url` until the range is covered; mapper produces
  canonical `Trade` (ns → ms → UTC).
- `fetch_orderbook_snapshot` maps the snapshot `book`/bbo fields to canonical
  `OrderBook`; if Massive doesn't expose a full L2 history the whole snapshot
  capability is declared honestly per sec 3.
- `fetch_fundamentals` returns canonical `Fundamentals` (base fields +
  asset-class block, sec 3/4); equity data from `ticker_details` fills
  currency/exchange/market_cap while `financials` feeds the extensions.
- `instrument()` uses ticker prefixes to classify: no prefix → equity,
  `X:` → crypto, `C:` → forex, `I:` → index (equity-like), `O:` → option,
  futures contract → `FutureExtension`. Canonical `Instrument` (sec 4).
- Caching uses `build_cache_key(provider="massive", ...)`; only closed bars
  cached (sec 17). Output format conversion via `ops/output.py` (sec 12).
- Errors: 401 → `AuthenticationError`, 429 → `RateLimitError(retry_after)`,
  404 → `SymbolNotFoundError`, 403 → `DataNotAvailableError`/`PaidTierRequiredError`,
  other → `ProviderError` (sec 15 + 22).
- Rate limiting: token bucket per instance (sec 16); the SDK's built-in retries
  are bypassed/controlled so DataKodo's `Config.max_retries`/
  `retry_base_delay` govern.

---

## 5. Parts shared with other exchange/broker implementations

Everything that is agnostic of Massive must live in core/ops and be reused, not
re-implemented:

- `AdapterInterface.fetch_ohlcv_batch` (sec 10) — concurrency + arbitrary
  output format.
- `ops/pagination.paginate` (sec 11) — date-range chunking. Support Massive's
  `next_url` cursor loop with the same `_fetch_chunk` callback pattern.
- `ops/resample` / `pick_source_timeframe` (sec 7) — used if a non-native
  timeframe is ever requested.
- `ops/validation` (sec 18) — `validate_ohlcv`, `drop_incomplete_bars`.
- `ops/output.to_output_format` (sec 12) — pandas/polars/arrow/numpy.
- `core/config.Config` (sec 13) — add `massive_api_key`, `massive_rate_limit_*`,
  `massive_timeout` fields (pattern: `binance_*`).
- `storage/parquet.ParquetBackend` + `storage/cache.build_cache_key` (sec 17).
- `ratelimit/limiter.TokenBucket` (sec 16).
- Canonical schemas/enums/instruments/exceptions (sec 2, 20).
- Streaming utilities in `streaming/` (reconnect, order book maintainer) — used
  by `MassiveWS` for clean reconnects.

The adapter only **wires** these together; each is tested once in its own suite.

---

## 6. Parts that must remain Massive-specific

- `MassiveREST` HTTP client: base URL `https://api.massive.com`, `Bearer`
  header, per-market ticker prefixes, `next_url` cursor decoding.
- `MassiveWS`: `wss://socket.massive.com/{market}`, auth handshake, channel
  reconciliation, asset-class channel mapping (`T.`/`XT.`/`CA.`/`V.`).
- `mapper.py`: currently-stubbed mappings are expanded for every asset class:
  - stocks/options/forex aggs, crypto aggs, futures aggs (`window_start` ns),
  - v3 trades/quotes (ns) for all classes,
  - snapshot/bbo → OrderBook,
  - ticker_details → Fundamentals extensions.
- Timeframe → `(multiplier, timespan)` mapping table (Massive-specific; core
  `timeframe.py` stays canonical).
- `instrument()` prefix parsing (`X:`, `C:`, `I:`, `O:`) and futures contract
  parsing (`GCJ5`).
- Resilience: Massive retry statuses 413/429/499/5xx; backoff tuned per
  `Config`.

---

## 7. Files/modules to create or modify

| File | Action |
| --- | --- |
| `src/datakodo/adapters/massive/adapter.py` | rewrite to implement full fetch/stream methods (currently thin stub) |
| `src/datakodo/adapters/massive/rest.py` | implement `aggs`, `trades`, `snapshot`, `ticker_details` (currently raise NotImplementedError) |
| `src/datakodo/adapters/massive/ws.py` | implement `trade_stream`/`orderbook_stream` over `socket.massive.com` |
| `src/datakodo/adapters/massive/mapper.py` | expand `map_ohlcv`/`map_trades`; add `map_orderbook`, `map_fundamentals`, `map_instrument` |
| `src/datakodo/adapters/massive/__init__.py` | export `MassiveAdapter` (mirror binance/mt5) |
| `src/datakodo/core/timeframe.py` | add `MASSIVE_TIMESPANS` mapping (or keep in adapter) |
| `src/datakodo/core/config.py` | add `massive_api_key`, `massive_timeout`, `massive_rate_limit_rate/burst` |
| `tests/adapters/test_massive.py` | expand from capability smoke tests to full contract tests |
| `tests/adapters/contract_tests.py` | unchanged (Massive already conforms once adapter is functional) |
| `tests/test_library_imports.py` | already lists `datakodo.adapters.massive.*` — kept |
| `pyproject.toml` | already renamed extra `massive = ["massive>=1.0"]` + mypy override |
| `docs/massive.md`, `docs/massive-python-client.md` | created (reference) |
| `README.md` | updated (provider table, install, docs links) |
| `CHANGELOG.md` | add Massive entry on implementation |

No new top-level packages or abstract layers are introduced. No factory, no
registry beyond `Client`'s provider registry in core.

---

## 8. Tests required

Follow the existing strategy (sec 26):

- **Contract conformance** (like `test_binance_contract.py`): mock
  `MassiveREST.aggs` with canonical agg JSON → `fetch_ohlcv` result has exactly
  the canonical columns, UTC tz, correct rows. Same for `fetch_ticks`/`map_trades`,
  `fetch_fundamentals`, snapshot→OrderBook.
- **Mapper unit tests** (like `tests/adapters/test_binance_config.py` style):
  prop-based `hypothesis` on `map_ohlcv`/`map_trades`/`map_orderbook` for
  missing fields and ns/ms timestamp handling.
- **REST client tests**: URL construction, params (multiplier/timespan,
  comparison suffixes), `next_url` pagination loop (2 pages → merged), error
  mapping (401/403/404/429 → DataKodo exceptions), rate-limiter integration.
- **WS tests**: handshake auth, subscribe/unsubscribe message shapes,
  channel→asset-class routing, `ev=="status"` filtering, mapper on `T`/`XT`/`Q`/
  `XL2` payloads.
- **Instrument tests**: prefix parsing (`AAPL`, `X:BTCUSD`, `C:EURUSD`,
  `I:SPX`, `O:...`, `GCJ5`) produces correct `Instrument` + extensions.
- **Lifecycle / config tests**: `massive_api_key` loaded from `.env`/env,
  defaults, `with adapter:` behavior.
- **Golden fixtures** (`tests/fixtures/`) with sanitized real Massive responses
  so CI runs without a key.
- **Live integration** gated (`.github/workflows/integration.yml`), not on every
  commit.

---

## 9. Implementation order

1. Core plumbing: add `massive_*` config fields; add `MASSIVE_TIMESPANS`
   mapping; confirm `ops/*` and `storage/*` are already adapter-agnostic.
2. `MassiveREST`:
   - `aggs()` with `next_url` cursor pagination + rate limiter + error mapping;
   - `trades()`, `snapshot()`, `ticker_details()`.
3. `mapper.py`: `map_ohlcv`, `map_trades`, `map_orderbook`, `map_fundamentals`,
   `map_instrument` (unit tested with hypothesis before wiring).
4. `MassiveAdapter`: `fetch_ohlcv` (persist/cache/output-format/resample hooks,
   mirroring Binance), `fetch_ticks`, `fetch_orderbook_snapshot`,
   `fetch_fundamentals`, `instrument`.
5. `MassiveWS`: `trade_stream`/`orderbook_stream` (auth + subscribe +
   reconnect via `streaming/`); wire `stream_trades`/`stream_orderbook`.
6. Contract + mapper + REST + WS tests; add Massive to
   `tests/test_library_imports.py` (already there).
7. `docs/massive.md` + `docs/massive-python-client.md` already written; add a
   usage section once methods are live. Update `CHANGELOG.md`.
8. Full QA: `pytest`, `ruff check`, `mypy src`; then commit/push on
   `feat/massive-rename-and-docs`.

Each step keeps the implementation simple and function-based where the codebase
allows it, consistent with the Binance/MT5 adapters.

---

## Cross-check vs. design rules

- Sec 2 capability declaration: `supports_*` flags match actual endpoints; never
  fake support.
- Sec 5 sync/async split: `fetch_*` sync, `stream_*` async generators.
- Sec 9 UTC only: all mapper functions convert Massive ts to UTC.
- Sec 15/22 errors + paid-tier honesty.
- Sec 26: contract suite + hypothesis + golden fixtures are the testing
  backbone.
- Sec 29: per-adapter docs updated in `docs/`.