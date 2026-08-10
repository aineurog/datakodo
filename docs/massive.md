# Massive.com API Reference

Massive is the rebranded Polygon.io (rebrand on **2025-10-30**). The API base
moved from `api.polygon.io` to `api.massive.com`. Existing API keys, accounts,
and integrations keep working unchanged; `api.polygon.io` stays supported for an
extended period.

Official docs: <https://massive.com/docs/llms.txt> (machine-readable index of
every page). Python SDK: <https://github.com/massive-com/client-python>.

---

## 1. Authentication

- API keys are created at <https://massive.com/dashboard/keys>.
- **Header auth (current client):** `Authorization: Bearer <API_KEY>`.
- The environment variable is `MASSIVE_API_KEY` (also used by DataKodo).
- Public market data needs an API key at Massive (unlike Binance).
- Free tier exists for many endpoints with limits; higher tiers unlock real-time
  data, longer history, and more endpoints.

Base URL: `https://api.massive.com`

---

## 2. Time Conventions

| Asset class | Aggregates timestamps | Notes |
| --- | --- | --- |
| Stocks / Options / Indices / Forex | `t` in **Unix milliseconds**, bars anchored to **Eastern Time (ET)** | Pre/regular/post-market sessions all included in stock aggs |
| Crypto | `t` in **Unix milliseconds**, bars anchored to **UTC** | 24/7 |
| Futures | `window_start` in **nanoseconds**, `session_end_date` in `YYYY-MM-DD` | Timestamps at candle start |

- Aggregates REST endpoints accept `from`/`to` as `YYYY-MM-DD` **or** a ms timestamp.
- Trades/quotes REST (v3) use **nanosecond** precision timestamps.
- DataKodo stores everything in UTC (design doc sec 9) — adapters convert.

---

## 3. Symbol Conventions (Ticker Prefixes)

Massive uses a prefix to indicate market. The aggregate endpoint
(`/v2/aggs/ticker/{ticker}/range/...`) accepts these same strings:

| Prefix | Asset class | Example |
| --- | --- | --- |
| *(none)* | Stocks | `AAPL`, `MSFT` |
| `X:` | Crypto | `X:BTCUSD` |
| `C:` | Forex | `C:EURUSD`, `C:USD/CAD` |
| `I:` | Indices | `I:SPX`, `I:NDX` |
| `O:` | Options contract | `O:AAPL241220C00150000` |
| *(none, e.g. `GCJ5`)* | Futures contract | `GCJ5` (April 2025 gold), `ESU5` |

Options contracts follow standard OCC format: `O:<UNDERLYING><YYMMDD><C/P><strike×10>`.

WebSocket channels use their own suffixes (section 7): `T.AAPL`, `XT.BTC-USD`,
`C.USD/EUR`, `CA.USD/CAD`, `I:SPX`, `O:...`.

---

## 4. REST API Structure

All REST responses share a common envelope:

```json
{
  "status": "OK",
  "request_id": "...",
  "results": [ ... ],        // or "results": { ... }, "tickers": [...]
  "resultsCount": 5,
  "count": 5,
  "next_url": "https://api.massive.com/v3/trades/AAPL?cursor=..."
}
```

Key envelope fields: `status`, `request_id`, `results`, `resultsCount`/`count`,
and **`next_url`** for pagination (section 4.1).

### 4.1 Pagination

- List endpoints return a `next_url` cursor when more pages exist.
- Follow `next_url` (it is fully self-contained; do not merge your own params)
  until it is absent.
- The official Python client does this automatically when `pagination=True`
  (the default); `limit` then controls **page size**, not the total.
- Set `pagination=False` to fetch a single page.

### 4.2 Timeframes ("aggregates")

Aggregates (OHLCV bars) use `multiplier` + `timespan`:

```text
GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}
```

- `timespan`: `second`, `minute`, `hour`, `day`, `week`, `month`, `quarter`, `year`
- `multiplier`: integer (e.g. `5` with `minute` = 5-minute bars)
- `adjust` controls split adjustment (stocks, default `true`).
- `sort`: `asc` or `desc`. `limit`: default 5000, **max 50000**.
- Aggregates are built from eligible trades only; empty intervals are omitted.

Futures differ (nanosecond + resolution string):

```text
GET /futures/v1/aggs/{ticker}?resolution={n}{unit}&window_start=...
```

- `resolution` = `{number}{unit}`, unit in `sec, min, hour, session, week, month,
  quarter, year` (e.g. `1min`, `1session`). Minute max 59 → then use hour.
- `window_start` accepts `YYYY-MM-DD` or a nanosecond timestamp, with comparison
  suffixes `.gt/.gte/.lt/.lte`.
- Response keys: `window_start` (ns), `open/high/low/close`, `volume`,
  `dollar_volume`, `transactions`, `session_end_date`, `settlement_price`.

### 4.3 Trades (v3)

```text
GET /v3/trades/{stockTicker}
```

Filters: `timestamp` (date or ns ts), `timestamp.gt/.gte/.lt/.lte`, `order`,
`sort`, `limit` (default 1000, **max 50000**).

Trade fields (stocks): `conditions` (list of codes), `exchange`, `id`, `price`,
`size`, `decimal_size`, `sip_timestamp` (ns), `participant_timestamp` (ns),
`sequence_number`, `tape`, `trf_*`.

Last trade: `GET /v2/last/trade/{ticker}`.

### 4.4 Quotes (v3)

```text
GET /v3/quotes/{stockTicker}
```

NBBO fields: `bid_exchange`, `bid_price`, `bid_size`, `ask_exchange`,
`ask_price`, `ask_size`, `conditions`, `indicators`, `sip_timestamp` (ns),
`participant_timestamp` (ns), `sequence_number`, `tape`.

Last quote: `GET /v2/last/nbbo/{ticker}` (stocks).

### 4.5 Reference / Tickers

```text
GET /v3/reference/tickers/{ticker}        # single ticker details (overview)
GET /v3/reference/tickers?market=...      # list all tickers
```

Ticker overview fields (stocks): `name`, `description`, `market`, `locale`,
`primary_exchange`, `currency_name`, `type`, `market_cap`, `cik`,
`composite_figi`, `weighted_shares_outstanding`, `total_employees`,
`ticker_root`, `ticker_suffix`, `active`, `list_date`, etc.

### 4.6 Other relevant REST endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/marketstatus/now` | Real-time market open/closed status |
| `GET /v1/marketstatus/upcoming` | Upcoming market holidays |
| `GET /v3/reference/exchanges` | Exchange ID → name mappings |
| `GET /v3/reference/conditions` | Trade/quote condition code glossary |
| `GET /v2/aggs/grouped/locale/{locale}/market/{market}/{date}` | Grouped daily bars |
| `GET /v2/aggs/ticker/{ticker}/prev` | Previous close bar |
| `GET /v1/open-close/{ticker}/{date}` | Daily open/close |
| `GET /v2/reference/news` | News |
| `GET /stocks/v1/financials` variants | Fundamentals (balance sheet, income, cash flow, ratios) |
| `GET /v2/snapshot/locale/{locale}/markets/{market}/tickers` | Snapshots per market |

The full endpoint catalogue (by asset class — stocks, options, futures, indices,
forex, crypto, economy, alternative/consumer data, partner datasets, SEC
filings, fundamentals) is listed in `docs/llms.txt` at massive.com.

---

## 5. Market-Data Categories & Plan Implications

Massive splits data into per-asset-class plans. A given plan restricts:

- **Plan access** — which endpoints are included at all.
- **Plan recency** — end-of-day / 15-minute delayed / 10-minute delayed /
  8-hour historical / real-time.
- **Plan history** — how far back records go (e.g. 2 years, 5 years, 10 years,
  all history).

Examples of the free/lowest tiers:

| Asset | Basic tier | Starter tier |
| --- | --- | --- |
| Stocks aggs | End-of-day, 2 years | 15-minute delayed, 5 years |
| Crypto aggs | End-of-day, 2 years | Real-time, all history |
| Forex aggs | End-of-day, 2 years | Real-time, all history |
| Futures aggs | 8-hour historical, 2 years | 10-minute delayed, 2 years |
| Stocks trades | Not included | (Starter) 15-min delayed |

Trades/quotes/quotes-streaming typically require *Stocks Advanced* or the
Business tier. DataKodo surfaces this via `requires_paid_tier` capability
metadata and raises `PaidTierRequiredError` (design doc sec 22).

---

## 6. Errors

REST returns non-200 statuses with a JSON body. Common codes:

| HTTP | Meaning |
| --- | --- |
| 400 | Bad request (bad timeframe/resolution, missing param) |
| 401 | Invalid API key |
| 403 | Forbidden / not licensed for the endpoint/tier |
| 404 | Ticker/contract not found |
| 429 | Rate limit exceeded |
| 5xx | Server error |

The official Python client collapses **all** non-200 responses into a single
`BadResponse` exception carrying the raw body. DataKodo maps responses onto its
own hierarchy (`AuthenticationError`, `RateLimitError`, `SymbolNotFoundError`,
`DataNotAvailableError`, `ProviderError` — design doc sec 15).

---

## 7. WebSocket API

### 7.1 Connection

```text
wss://socket.massive.com/{market}
```

`market` ∈ `stocks | options | forex | crypto | indices | futures | futures/cme
| futures/cbot | futures/nymex | futures/comex`.

Alternative feeds: `delayed.massive.com`, `nasdaqfeed.massive.com`,
`polyfeed.massive.com`, plus business/IEX/EDGX variants for paid tiers.

### 7.2 Handshake

```json
{"action": "auth", "params": "<API_KEY>"}
```

Reply is a status message; `"status": "auth_failed"` ⇒ bad key.

### 7.3 Subscribing / Unsubscribing

```json
{"action": "subscribe", "params": "T.AAPL,T.MSFT"}
{"action": "unsubscribe", "params": "T.AAPL"}
```

- Multiple subscriptions comma-joined. `*` wildcards subscribe to everything in
  that channel (e.g. `T.*`).
- A channel + symbol pair is formatted `<CHANNEL>.<SYMBOL>`.
- On reconnect you must re-auth and re-subscribe (client keeps the desired set).

### 7.4 Channels & Message Shape

Messages arrive as **arrays of objects**; each object carries `ev` (event type).
All timestamps in Unix ms unless noted.

| Market | Channel | `ev` | Fields |
| --- | --- | --- | --- |
| Stocks | `T.*` trades | `T` | `sym, x(Exchange), i(ID), z(tape), p(price), s(size), ds, c(conds), t(SIP ms), pt, q(seq), trfi, trft` |
| Stocks | `Q.*` quotes | `Q` | `sym, bx, bp, bs, ax, ap, as, c, i, t(ms), q, z` |
| Stocks | `A.*` per-second aggs | `A` | `sym, o, h, l, c, v, av, op, vw, a, z, s(start ms), e(end ms), otc` |
| Stocks | `AM.*` per-minute aggs | `AM` | same shape as `A` |
| Stocks | `LULD.*` | `LULD` | limit-up/limit-down events |
| Stocks | `FMV.*` | `FMV` | fair market value |
| Stocks | `NOI.*` | `NOI` | net order imbalance |
| Options | `T.O:...` etc. | `T` | option trades share the `T` shape |
| Crypto | `XT.*` trades | `XT` | `pair(from-to), p, s, c(1=sell,2=buy), t(ms), i, x, r` |
| Crypto | `XQ.*` quotes | `XQ` | `pair, bp, bs, ap, as, t, x, r` |
| Crypto | `XA.*` per-minute aggs | `XA` | `pair, o, h, l, c, v, vw, s, e, z` |
| Crypto | `XAS.*` per-second aggs | `XAS` | same shape as `XA` |
| Crypto | `XL2.*` level-2 book | `XL2` | `pair, b, a, t, x, r` |
| Forex | `C.*` quotes | `C` | `pair(USD/EUR), p, a, b, t` |
| Forex | `CA.*` per-minute aggs | `CA` | `pair, o, h, l, c, v, vw, s, e, z` |
| Forex | `CAS.*` per-second aggs | `CAS` | same shape as `CA` |
| Indices | `V.*` values | `V` | `val, T, t` |
| Indices | `A.I:*` / `AM.I:*` | `A`/`AM` | index aggs |

Crypto condition codes: `0`/empty = empty/no side, `1` = sellside, `2` = buyside
(feed may tag the buyer).

### 7.5 WebSocket status messages

Status messages (`ev == "status"`) carry: `status` (`connected`/`auth_success`/
`auth_failed`/`error`), `message`, and `request_id`. They should be filtered out
of the data path.

---

## 8. Rate Limits

- Massive is quota-based per plan; the free tier can hit 429s.
- The official client retries `413, 429, 499, 500, 502, 503, 504` with a default
  `backoff_factor=0.1` (0.1s, 0.2s, 0.4s, ...) and `retries=3`.
- DataKodo gates all requests through a token bucket (design doc sec 16) and
  auto-retries `RateLimitError` with configurable exponential backoff.

---

## 9. Python Client (summary)

```python
from massive import RESTClient, WebSocketClient

rest = RESTClient(api_key="...")
aggs = list(rest.list_aggs(ticker="AAPL", multiplier=1, timespan="minute",
                           from_="2023-01-01", to="2023-06-13", limit=50000))

ws = WebSocketClient(api_key="...", subscriptions=["T.AAPL"])
ws.run(handle_msg=lambda msgs: print(msgs))
```

Full detail — constructor args, method signatures, defaults, models — is in
[docs/massive-python-client.md](massive-python-client.md).

---

## 10. Key Takeaways for the Massive Adapter

1. **One aggregates endpoint serves all asset classes** — the ticker prefix
   (`AAPL`, `X:BTCUSD`, `C:EURUSD`, ...) selects the market; only futures use a
   different endpoint (`/futures/v1/aggs/{ticker}`).
2. **Timestamps differ by market**: ms + ET for stocks/forex, ms + UTC for
   crypto, ns for v3 trades/quotes and futures. Every path normalizes to UTC.
3. **Pagination** is cursor-based (`next_url`) — reuse one helper, not per-endpoint
   loops.
4. **Asset-class-specific symbols** (options `O:...`, futures `GCJ5`) must map
   onto the canonical `Instrument` model with typed extensions.
5. **Real-time tiers** are gated; adapters must declare
   `requires_paid_tier`/`supports_*` honestly and raise `PaidTierRequiredError`
   (design doc sec 22).
6. **WebSocket channels are per asset class** — `T.` vs `XT.` vs `CA.` vs `V.`
   are structurally different (prefix, fields, symbol format) and should not be
   blindly unified in the adapter.

---

## 11. Source Links

- Docs index: <https://massive.com/docs/llms.txt>
- Rebrand note: <https://massive.com/blog/polygon-is-now-massive/>
- Python SDK: <https://github.com/massive-com/client-python>