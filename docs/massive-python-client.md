# Massive Python Client — Reference

Reference for the official Massive (formerly Polygon.io) Python SDK:
<https://github.com/massive-com/client-python>. Package on PyPI: **`massive`**
(rebranded from `polygon-api-client`); import name is also `massive`.

---

## 1. Install

```bash
pip install -U massive
```

Requires Python 3.9+. Dependency `websockets>=13` (new `websockets.asyncio` API).

---

## 2. Public API surface

```python
from massive import RESTClient, WebSocketClient
from massive.exceptions import AuthError, BadResponse
```

`massive/__init__.py` exports: `RESTClient`, `WebSocketClient`, `version`, and
all exceptions.

---

## 3. RESTClient

### 3.1 Constructor

```python
RESTClient(
    api_key: Optional[str] = os.getenv("MASSIVE_API_KEY"),
    connect_timeout: float = 10.0,
    read_timeout: float = 10.0,
    num_pools: int = 10,
    retries: int = 3,
    base: str = "https://api.massive.com",
    pagination: bool = True,
    verbose: bool = False,
    trace: bool = False,
)
```

Notes:

- Default base **`https://api.massive.com`**; pass `base=` to override.
  There is **no automatic `api.polygon.io` fallback**.
- `api_key=None` raises `AuthError`.
- Auth header: `Authorization: Bearer <API_KEY>` (not a query param).
- `trace=True` + `verbose=True` logs full request URL + headers (key redacted).

### 3.2 HTTP / retry behavior

- Uses `urllib3.PoolManager` with `Retry(total=3, status_forcelist=[413, 429,
  499, 500, 502, 503, 504], backoff_factor=0.1)` → sleeps 0.1/0.2/0.4/0.8/1.6s.
- Any response `!= 200` raises `BadResponse(raw_body)`. Only two exceptions
  exist: `AuthError`, `BadResponse` (see section 6).
- Datetimes sent as timestamps: `_get_params` converts `datetime` → int (
  `nanos` default, `millis` in aggs, via `time_mult`).

### 3.3 Parameter translation conventions

- Comparison operators: `timestamp_lt` → `timestamp.lt`; also `_gt`, `_gte`,
  `_lte`.
- `*_any_of` → comma-joined string.
- Bools → `"true"/"false"`; enums → `.value`.

### 3.4 Pagination semantics

- `pagination=True` (default): follow `next_url` until exhausted. `limit` = page
  size, **not** total count.
- `pagination=False`: one page only.
- Pass `limit=50000` for aggs / `limit=50000` for trades+quotes to minimize calls.

### 3.5 Key methods (grouped by client)

**Aggregates / OHLCV:**

```python
list_aggs(ticker, multiplier, timespan, from_, to, adjusted=None, sort=None,
          limit=None, ...)                    # /v2/aggs/ticker/{t}/range/{m}/{t}/{from}/{to}  (iterator)
get_aggs(...)                                 # same URL, single page -> List[Agg]
get_grouped_daily_aggs(date, locale="us", market_type="stocks", ...)
get_daily_open_close_agg(ticker, date, ...)   # /v1/open-close/{ticker}/{date}
get_previous_close_agg(ticker, ...)           # /v2/aggs/ticker/{ticker}/prev
```

Crypto/forex/indices reuse `list_aggs` with prefixed tickers:
`X:BTCUSD`, `C:EURUSD`, `I:SPX`.

**Trades:**

```python
list_trades(ticker, timestamp=None, *[_lt/_lte/_gt/_gte], limit=None, ...)
get_last_trade(ticker, ...)                   # /v2/last/trade/{ticker}
get_last_crypto_trade(from_, to, ...)         # /v1/last/crypto/{from}/{to}
```

**Quotes:**

```python
list_quotes(ticker, timestamp=?, *[_lt/_lte/_gt/_gte], limit, sort, order, ...)
get_last_quote(ticker, ...)                   # /v2/last/nbbo/{ticker}
get_last_forex_quote(from_, to, ...)
get_real_time_currency_conversion(from_, to, amount=None, precision=2, ...)
```

**Reference / tickers:**

```python
list_tickers(ticker=None, type=None, market=None, exchange=None, cusip=None,
             cik=None, date=None, active=None, search=None, limit=10, ...)
get_ticker_details(ticker=None, date=None, ...)   # /v3/reference/tickers/{ticker}
list_ticker_news(ticker=None, ...)
get_ticker_types(...)
get_related_companies(ticker=None, ...)
list_splits(...) / list_dividends(...) / list_conditions(...) / get_exchanges(...)
```

**Snapshots (options + crypto + indices):**

```python
list_universal_snapshots(type=None, ticker_any_of=None, ...)   # /v3/snapshot
get_snapshot_all(market_type, tickers=None, ...)
get_snapshot_ticker(market_type, ticker, ...)                 # current snapshot for a ticker
get_snapshot_direction(market_type, "gainers"|"losers", ...)
get_snapshot_option(underlying_asset, option_contract, ...)
list_snapshot_options_chain(underlying_asset, params=...)
get_snapshot_crypto_book(ticker, ...)                          # /v2/snapshot/.../book
get_snapshot_indices(...)
```

**Options contracts:**

```python
get_options_contract(ticker, as_of=None, ...)
list_options_contracts(underlying_ticker=?, contract_type=?, expiration_date=?,
                       as_of=?, strike_price=?, expired=?, limit, sort, order, ...)
```

**Futures:**

```python
list_futures_aggregates(ticker, resolution, window_start=?, limit, sort, ...)
list_futures_contracts(...)
list_futures_products(...)
list_futures_quotes(ticker, ...) / list_futures_trades(ticker, ...)
list_futures_schedules(...) / list_futures_market_statuses(...)
get_futures_snapshot(...) / list_futures_exchanges(...)
```

**Indicators (server-side technicals — not part of DataKodo scope):**

```python
get_sma(ticker, timespan, window, ...)
get_ema(...) / get_rsi(...) / get_macd(short_window, long_window, signal_window, ...)
```

**Fundamentals (stocks):**

```python
list_financials_balance_sheets(...)
list_financials_cash_flow_statements(...)
list_financials_income_statements(...)
list_financials_ratios(...)
list_short_interest(...) / list_short_volume(...)
vx.list_stock_financials(...)   # client.vx = experimental VXClient
```

### 3.6 RequestOptionBuilder

`RESTClient(options=RequestOptionBuilder(edge_id, edge_ip_address, edge_user))`
sets `X-Massive-Edge-*` headers (Launchpad feed / edge routing).

---

## 4. WebSocketClient

### 4.1 Constructor

```python
WebSocketClient(
    api_key: Optional[str] = os.getenv("MASSIVE_API_KEY"),
    feed="socket.massive.com",          # or Feed enum (Delayed/RealTime/Nasdaq/...)
    market="stocks",
    raw=False,
    verbose=False,
    subscriptions=None,                 # e.g. ["T.AAPL", "T.*"]
    max_reconnects=5,
    secure=True,
)
```

- URL: `wss://{feed}/{market}` when `secure=True`.
- `api_key=None` → `AuthError`.

### 4.2 Handshake and subscription reconciliation

- Server handshake: send `{"action": "auth", "params": "<API_KEY>"}`.
  `status == "auth_failed"` → `AuthError`.
- The client maintains a desired subscription set and reconciles diffs:
  `{"action": "subscribe", "params": "T.AAPL,T.MSFT"}` /
  `{"action": "unsubscribe", "params": "..."}`.
- `subscribe("T.*")` replaces existing `T.*`-scope subscriptions.

### 4.3 Running

```python
def handle_msg(msgs):            # List[WebSocketMessage]
    print(msgs)

ws.run(handle_msg=handle_msg)    # blocking; asyncio loop inside
# or async:  await ws.connect(processor=async_handle, ...)
ws.subscribe("Q.AAPL")           # add live
ws.unsubscribe("Q.AAPL")
ws.unsubscribe_all()
```

Messages are **arrays of typed models** (or raw strings with `raw=True`).
Status events (`ev == "status"`) are filtered out of the data path.

### 4.4 Message models (`massive.websocket.models`)

| Event class | `ev` values | Key fields |
| --- | --- | --- |
| `EquityAgg` | `A`, `AM` | `sym, o, h, l, c, v, av, op, vw, a, z, s, e, otc, dv, dav` |
| `EquityTrade` | `T` | `sym, x, i, z, p, s, ds, c, t, q, trfi, trft` |
| `EquityQuote` | `Q` | `sym, bx, bp, bs, ax, ap, as, c, i, t, z, q` |
| `CurrencyAgg` | `XA`, `XAS`, `CA`, `CAS` | `pair, o, c, h, l, v, vw, s, e, z` |
| `CryptoTrade` | `XT` | `pair, x, i, p, s, c, t, r` |
| `CryptoQuote` | `XQ` | `pair, bp, bs, ap, as, t, x, r` |
| `ForexQuote` | `C` | `pair, p, x, a, b, t` |
| `FuturesAgg/Trade/Quote` | `A`, `AM`, `T`, `Q` | futures variants |
| `IndexValue` | `V` | `val, t` |
| `Level2Book` | `XL2` | `pair, b, a, t, x, r` |
| `FairMarketValue` | `FMV` | `fmv, sym, t` |
| `Imbalance` | `NOI` | imbalance fields |
| `LimitUpLimitDown` | `LULD` | luld fields |

### 4.5 Channels by market

| Market | Channels |
| --- | --- |
| Stocks | `A.*` / `AM.*` / `T.*` / `Q.*` / `LULD.*` / `FMV.*` / `NOI.*` |
| Options | `A.*` / `AM.*` / `T.O:...` / `Q.*` / `FMV.*` |
| Indices | `A.I:*` / `AM.I:*` / `V.*` |
| Crypto | `XA.*` / `XAS.*` / `XT.*` / `XQ.*` / `XL2.*` / `FMV` |
| Forex | `CA.*` / `CAS.*` / `C.*` |
| Futures (+ `/cme`, `/cbot`, `/nymex`, `/comex`) | `A.*` / `AM.*` / `T.*` / `Q.*` |

`Feed` enum values include `delayed.massive.com`, `socket.massive.com`,
`nasdaqfeed.massive.com`, `polyfeed.massive.com`, `polyfeedplus.massive.com`,
`starterfeed.massive.com`, `launchpad.massive.com`, `business.massive.com`,
plus IEX/EDGX/Nasdaq-basic business variants (paid).

`Market` enum: `stocks, options, forex, crypto, indices, futures, futures/cme,
futures/cbot, futures/nymex, futures/comex`.

---

## 5. Reconnect behavior

- `ConnectionClosedError` → reconnect counter; re-subscribes the full desired
  set, auto-reconnects while `reconnects <= max_reconnects` (default 5).
- `ConnectionClosedOK` → clean exit.
- The recv loop polls at 1-second intervals (`asyncio.wait_for`) so live
  `subscribe`/`unsubscribe` land within ~1 s.

---

## 6. Exceptions

The SDK exposes **only two**:

```python
class AuthError(Exception):      # missing key, or WS handshake auth_failed
class BadResponse(Exception):    # any non-200 REST response; message = raw body
```

The old polygon-client hierarchy (`BadRequest`, `AuthenticationError`,
`ForbiddenError`, `NotFoundError`, `TooManyRequestsError`, ...) is gone. Callers
must inspect the body/status themselves. DataKodo maps these onto its own
hierarchy inside the adapter (never exposed to users).

---

## 7. Relevance to DataKodo

Useful to drive `MassiveAdapter`:

- `list_aggs` / `get_aggs` → `fetch_ohlcv` (all asset classes via ticker prefix;
  chase `next_url` for full ranges).
- `list_trades` → historical `fetch_ticks` (v3, nanosecond precision).
- `get_ticker_details` / `list_tickers` → reference data / `passes` fundamentals.
- `get_snapshot_ticker` / `get_snapshot_all` → `fetch_orderbook_snapshot`-style
  snapshots where supported (`supports_orderbook_snapshot`).
- `WebSocketClient(subscriptions=["T.*"])` → `stream_trades`; `Q.*`/`XL2.*` →
  `stream_orderbook`; `A.*`/`AM.*` → optional ohlcv streaming.
- `massive` (the client) is a **dependency**, but the adapter should still expose
  raw dicts through `MassiveREST`/`MassiveWS` and normalize with `mapper.py`,
  matching the Binance/MT5 pattern (design doc sec 2: adapters import canonical
  schema, never their own).