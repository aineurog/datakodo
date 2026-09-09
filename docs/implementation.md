# Massive Module — Implementation Plan & Tracker

How the Massive (Massive.com, ex-Polygon.io) adapter is built, step by step,
strictly following `docs/design-document.md` and the Massive AI skill
(`.opencode/skills/massive_ai_skill.md`). Each step has a checklist so
completion is unambiguous: `✅` done and verified, `❌` todo.

> Scope: steps 1–12 cover **REST** (sec 6: sync historical first).
> Step 13 covers **WebSocket streaming** (`stream_trades`).

## Status: what is DONE vs what is REMAINING

**DONE (verified in code):**

| # | Step | Proof |
|---|---|---|
| 1 | Skeleton & capabilities | flags + entry points + `datakodo[massive]` extra live-checked |
| 2 | Config & auth | env fallback / explicit-wins / lazy init live-checked |
| 3 | Single-class REST transport | live fetch OK, 17/17 tests, ruff+mypy clean |
| 4 | Timeframe mapping | all 9 canonical timeframes resolve, zero missing |
| 5 | `fetch_ohlcv` (basic/all/list, `is_closed`, validation) | mocked matrix pass + live AAPL/BTC/EURUSD |
| 6 | Dynamic mapper (None-safe, pass-through, dedup) | all-None row, unknown-field, dup-timestamp cases pass |
| 7 | Search & instrument mapping | mocked suite green |
| 8 | Fundamentals (+ mypy fixes) | signatures verified, mypy clean |
| 9 | Historical ticks (code-complete) | unit-verified; live gated by free-tier plan |
| 10 | Core 30-day default range (all 5 adapters) | call sites grepped, 30d span live-checked |
| 11 | Quality gates | ruff + mypy (52 files) + 23/23 tests green |

**REMAINING:**

| # | Item | Blocked by |
|---|---|---|
| 12a | Paid-tier entitlements (`list_trades`, `I:SPX`, some Options) | Plan upgrade, then re-run live suite |
| 12b | Golden fixtures for contract tests (sec 26) | Recording sanitized payloads |
| 12c | PR merge `massive` → `main` | Items above |
| 13 | **WebSocket streaming** (`connect`, subscribe, `trade_stream`, reconnect, offline + live tests) | Port-over from `test/ws-library` prototype + review |

---

## Step 1 — Adapter skeleton & capability declaration (sec 2)

One class implementing `AdapterInterface`; every capability declared honestly,
unsupported ones fail clearly instead of faking support.

- ✅ `MassiveAdapter` with flags: `supports_ohlcv`, `supports_ticks`,
      `supports_streaming_ticks`, `supports_fundamentals = True`;
      order-book flags `False` (verified live against the class)
- ✅ Registered via entry points (`pip install datakodo[massive]`;
      confirmed in `pyproject.toml:49-52` + optional-dependencies)
- ✅ `concurrency_model = "thread"` (REST is stateless/thread-safe, sec 23)

## Step 2 — Config & auth (sec 14/15)

Provider settings on its own object; keys via explicit arg → env → `.env`.

- ✅ `MassiveConfig` (`MASSIVE_` prefix, `.env` support): `api_key`,
      `base_url`, `timeout`, `rate_limit_rate`/`burst` (free-tier tuned)
- ✅ Explicit `api_key=` wins over environment (sec 15 precedence —
      verified: env fallback works, explicit overrides, empty key inits fine)
- ✅ `AuthenticationError` only when a call actually needs a missing key

## Step 3 — REST transport: one class on the official client (sec 16/17)

Per the skill: official client libraries are the foundation — auth, retries,
pagination, parsing come from them; DataKodo adds gating + error mapping.
Single class like `BinanceREST`/`MT5REST` (no wrapper split).

- ✅ `MassiveREST(RESTClient)`: `retries=0`, `pagination=True`
- ✅ `_get` choke point: token-bucket gate → backoff retry → status mapping
- ✅ Status map: 400→`DataValidationError`, 401→`AuthenticationError`,
      403→`PaidTierRequiredError`, 404→`SymbolNotFoundError`,
      429→`RateLimitError(retry_after)`, 5xx→`ProviderError`→`RetriesExhaustedError`
- ✅ No dead code: no duplicated `BASE_URL` (library already defaults it),
      no unused retry constants

## Step 4 — Timeframe mapping (sec 19)

- ✅ `MASSIVE_MAP`: canonical `1m…1mo` → `(multiplier, timespan)` pairs
      (verified: zero missing `Timeframe` members; `1h→(1,'hour')`,
      `1mo→(1,'month')`)
- ✅ `massive_resolution()`; unknown timeframe → `ValueError` → adapter
      raises `InvalidTimeframeError`

## Step 5 — `fetch_ohlcv` (sec 3/6/18)

Sync historical first (sec 6); canonical frame
`timestamp, open, high, low, close, volume, is_closed` (sec 3/10: UTC,
bar-open anchor).

- ✅ `fetch_ohlcv(symbol, timeframe, start, end)` with `columns="basic"|"all"|[...]`
      (verified mocked: basic → 7 base cols; `all` → +session/vwap; `['vwap']` → base+vwap)
- ✅ `is_closed` derived (bar open + one candle ≤ now); `include_live` opt-in
      (verified: 2/2 closed on past bars)
- ✅ `validate_ohlcv` before return; `to_output_format` (pandas/polars/arrow)
- ✅ Empty result → `DataNotAvailableError` (import fixed — was `NameError`;
      verified mocked: empty raises)
- ✅ `adjust` flag passed through for equities
- ✅ Verified live: AAPL (stocks), `X:BTCUSD` (crypto), `C:EURUSD` (forex)

## Step 6 — Dynamic mapper (sec 3, asset-aware)

No hardcoded output shape; Forex/Crypto/Indices/Options differences need no
code changes.

- ✅ `_RAW_MAP` (`t/o/h/l/c/v/vw/n/otc` → canonical); unknown keys pass through
      (verified: all-None row maps without crash; `future_field` passes through)
- ✅ None-safe float conversion (fixed `I:NDX` `TypeError` crash; verified live: 9 rows)
- ✅ Timestamp-dedup on stitch (sec 12: verified 3 rows/1 dup → 2 rows out)
- ✅ `columns="all"` returns base + every extra the endpoint actually sent
      (verified: 12 cols incl. `otc`, `future_field`)

## Step 7 — Instrument search & mapping (sec 4/5)

- ✅ `search_instruments(query, asset_class, instrument_type, quote, exchange, limit)`
      over `list_tickers`, fresh fetch per call (no ticker caching)
- ✅ `map_instrument`: prefix taxonomy (`X:`/`C:`/`I:`/`O:`/unprefixed),
      `AssetClass` × `InstrumentType` never conflated (sec 4)

## Step 8 — Fundamentals (sec 3)

Reference data: common base + asset-class reality, never a fake unified shape.

- ✅ `fetch_fundamentals` via `ticker_details` (+ `ensure_symbol_known`)
- ✅ Mypy fixes: signature matches base (`str | Instrument, **kwargs`),
      correct `ticker_details(symbol)` call-site
- ✅ Unknown symbol → `SymbolNotFoundError`

## Step 9 — Historical ticks (sec 7)

Heavy volume, chunked pagination, opt-in. `supports_ticks=True` is now honest
(it previously had no override → base raised `NotSupportedError`).

- ✅ `MassiveREST.list_trades` (`timestamp.gte/lte`, paginated)
- ✅ `map_rest_trades` → canonical `Trade` (ns timestamps, `side=None` for
      stocks, non-numeric `id` → `trade_id=None`); REST shape kept separate
      from the WS `map_trades` socket shape
- ✅ `MassiveAdapter.fetch_ticks(symbol, start=None, end=None, limit)` 
- ✅ Unit-verified offline with synthetic rows

## Step 10 — Core default date range (all providers)

Omit `start`/`end` → last 30 days (`end` = now UTC). One shared helper so
behaviour is identical everywhere.

- ✅ `core.timeframe.resolve_date_range()` (+ `DEFAULT_FETCH_DAYS = 30`,
      naive→UTC, `start > end` → `ValueError`)
- ✅ Wired into base `fetch_ohlcv_batch`, `Client` facade (deduped onto the
      helper), and all 5 adapters (Massive, Binance, MT5, Alpaca, IBKR —
      verified via grep: helper def + call sites in all 5 + batch)

## Step 11 — Quality gates (sec 26 + CI)

- ✅ `ruff check` clean; `ruff format` clean (re-run this session)
- ✅ `mypy src/datakodo/` clean (52 files, re-run this session)
- ✅ `tests/adapters/test_massive.py` **17/17 offline** + `test_timeframe.py`
      6/6 (23/23 combined, re-run this session) — stale live-call tests
      replaced with mocked-REST tests per repo convention (sec 26:
      mocked responses, no keys/network in CI)
- ✅ Live verification script `test_massive_ohlcv.py` (1h OHLCV + tick probe,
      all 5 asset classes, graceful paid-tier errors)

## Step 12 — Remaining / deferred (non-WS)

- ❌ **Paid-tier entitlements** — `list_trades`, `I:SPX`, some Options return
      `403` on the free key. Not a code gap (honest `PaidTierRequiredError`
      per sec 22); needs a plan upgrade. Re-run live suite after.
- ❌ **Golden fixtures** (sec 26) — record sanitized `aggs`/`trades` payloads
      for contract tests.
- ❌ **Merge `massive` → `main`** (PR) once the above land.

## Step 13 — WebSocket streaming (sec 6/7)

Async-native live trades (sec 6: `.stream*()` are always async generators;
sec 7: ticks streaming is the native WS use case). Current state on this
branch: `MassiveWS` is a stub (`trade_stream` raises `NotImplementedError`);
a full client exists only on the unmerged `test/ws-library` branch.

Already in place:

- ✅ Interface wiring: `adapter.stream_trades` → `ws.trade_stream` →
      `map_trades` (canonical `Trade`)
- ✅ Socket-shape mapper: `map_trades` handles stocks (`T.*`) and crypto
      (`XT.*`) frames (price/size/side/id)
- ✅ `supports_streaming_ticks = True` declared
- ✅ Prototype client on `test/ws-library` (connect, auth handshake,
      subscribe/unsubscribe, `trade_stream` with status-message filtering,
      context manager) — needs port-over + review, not blind merge

To implement (port from prototype, then verify):

- ❌ **`connect(market)`** — open `wss://socket.massive.com/{market}` for
      `stocks | options | forex | crypto | indices | futures…`; send
      `{"action": "auth", "params": api_key}` handshake; track connection state
- ❌ **`subscribe(channel, symbol)` / `unsubscribe`** — `T` (trades; later
      `Q` quotes etc.), `f"{channel}.{symbol}"` params, provider-prefixed
      symbols (`X:`, `C:`, `I:`, `O:`); no-op on duplicate (un)subscribe
- ❌ **`trade_stream(symbol)`** — subscribe `T`, yield `map_trades` per frame;
      skip `:`-prefixed heartbeats, invalid JSON, `ev == "status"` messages;
      unsubscribe on exit
- ❌ **Reconnect with backoff** on drop/timeout (bounded; surfaces clearly
      when exhausted). Full L2 delta-reconstruction stays out of scope
      (sec 30) — trades need resume, not deltas
- ❌ **Fix the known prototype bug**: port must be `443`, never derived from
      the hostname (caused DNS `gaierror` in early tests)
- ❌ **Offline tests** — mocked WS frames → `map_trades` → canonical `Trade`;
      connect/subscribe state machine with faked transport (no network in CI)
- ❌ **Live verification** — auth + subscribe `T.AAPL`, receive real trades
      (needs a plan with WS entitlement); market-closed must still connect
      (quotes may idle — heartbeats filtered, no false "no data")

---

## Quick reference (skill essentials)

```python
from datetime import UTC, datetime
from datakodo import Client

with Client("massive", api_key="...") as client:  # or MASSIVE_API_KEY env
    df = client.fetch_ohlcv("AAPL", "1h")  # last 30d by default
    df = client.fetch_ohlcv("X:BTCUSD", "1d", start, end, columns="all")
    trades = client.fetch_ticks("AAPL", limit=100)  # paid tier
    insts = client.search_instruments("AAPL")
```

Symbols: `AAPL` (stocks), `X:BTCUSD` (crypto), `C:EURUSD` (forex),
`I:SPX` (indices, paid), `O:…` (options, partially gated). All timestamps UTC.
