# DataKodo Usage Guide — Massive (Massive.com, ex-Polygon.io)

This page covers the **Massive** adapter: equities, forex, crypto, indices,
options, and futures through the canonical DataKodo API (design doc sec 29:
what is supported, what is not, capability flags, cost tier notes, restatement).

## Installation

```bash
pip install datakodo[massive]
```

## Quick Start

```python
from datetime import UTC, datetime, timedelta
from datakodo import Client

with Client("massive", api_key="...") as client:  # or MASSIVE_API_KEY env
    end = datetime.now(UTC)
    start = end - timedelta(days=7)
    df = client.fetch_ohlcv("AAPL", "1h", start, end)
```

Symbols: `AAPL` (stocks), `X:BTCUSD` (crypto), `C:EURUSD` (forex),
`I:SPX` (indices, paid), `O:…` (options, partially gated).

## Capabilities

| Capability | Supported | Notes |
|---|---|---|
| `fetch_ohlcv` | ✅ | All 9 canonical timeframes native; `columns="basic"/"all"/[...]`; `adjust` for equities; `is_closed` derived; UTC bar-open anchor |
| `fetch_ticks` (historical) | ✅ | REST `list_trades` → canonical `Trade`; often paid-tier gated → honest `PaidTierRequiredError` |
| `stream_trades` | ✅ | Async generator; `T` channel for stocks/options, `XT` for crypto; forex/indices raise `NotSupportedError` (no provider trades feed) |
| `fetch_orderbook_snapshot` / `stream_orderbook` | ❌ | Provider offers no historical L2; `NotSupportedError` by design |
| `fetch_fundamentals` | ✅ | `ticker_details` → canonical `Fundamentals`; unknown → `SymbolNotFoundError` |
| `search_instruments` | ✅ | Fresh `list_tickers` per call; substring on symbol **and** name; combinable `asset_class`/`instrument_type`/`quote`/`exchange` filters |
| `fetch_ohlcv_batch` | ✅ | Inherited thread-pool fan-out (adapter is `concurrency_model="thread"`) |

## Timeframes

`1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1mo` → Massive `(multiplier, timespan)`.
Unknown timeframe → `InvalidTimeframeError`.

## Tick-to-tick (historical + streaming)

```python
# Historical ticks (sync, opt-in, heavy volume paginated by the provider)
trades = client.fetch_ticks("AAPL", limit=100)  # list[Trade]: timestamp/price/size/side/trade_id
# side is None for stocks (no aggressor side on the feed);
# crypto maps condition codes 1 -> sell, 2 -> buy (int or [int] wire shape).

# Live ticks (async generator, native WS use case)
async for trade in client.stream_trades("X:BTCUSD"):
    print(trade.timestamp, trade.price, trade.size, trade.side)
```

Forex (`C:…`) and indices (`I:…`) have no WS trades feed (quotes/values
only), so `stream_trades` raises `NotSupportedError` there instead of idling.

## Cost tier notes

- Public market data needs a key (`MASSIVE_API_KEY`); explicit `api_key=`
  wins over env/`.env`.
- `list_trades`, `I:SPX`, and some options return HTTP 403 on the free plan →
  `PaidTierRequiredError` with a pricing link, never faked data.
- Free-tier rate limit is token-bucket gated (~6/min burst 5); 429s retry with
  backoff, exhaustion → `RetriesExhaustedError`.

## Restatement

Crypto closed bars are effectively never revised; equities/futures are
restated (corrections, corporate actions). No local cache is kept — every
fetch returns the provider's latest truth. `adjust=True` (default) requests
split/dividend-adjusted equity bars.

## Output format

pandas (default), polars, or arrow — globally via `Config(output_format=…)`
or per call via `output_format=…`.
