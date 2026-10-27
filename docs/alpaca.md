# DataKodo Usage Guide — Alpaca (Alpaca Markets)

This page covers the **Alpaca** adapter: US equities and crypto through the
canonical DataKodo API (design doc sec 29: what is supported, what is not,
capability flags, cost tier notes, restatement).

## Installation

```bash
pip install datakodo[alpaca]
```

## Quick Start

```python
from datetime import UTC, datetime, timedelta
from datakodo import Client

with Client("alpaca") as client:  # APCA_API_KEY_ID / APCA_API_SECRET_KEY env
    end = datetime.now(UTC)
    start = end - timedelta(days=7)
    df = client.fetch_ohlcv("AAPL", "1h", start, end)
```

Symbols: `AAPL` (stocks), `BTC/USD` (crypto).

## Capabilities

| Capability | Supported | Notes |
|---|---|---|
| `fetch_ohlcv` | ✅ | All 12 canonical timeframes native; `columns="basic"/"all"/[...]`; `feed` (`iex` default, `sip` paid); `adjustment` `raw`/`split`/`dividend`/`all` for equities; `is_closed` derived from the bar's session close; bar-open anchor (daily and larger moved to the session open) |
| `fetch_ticks` (historical) | ✅ | REST trades → canonical `Trade` (`side` always None — the feed carries none); with `start` the whole range is paged, without it `limit` caps the most recent trades; often paid-tier gated → honest `PaidTierRequiredError` |
| `stream_trades` | ✅ | Async generator over the official SDK streams; stocks + `BTC/USD`-style crypto |
| `fetch_orderbook_snapshot` / `stream_orderbook` | ❌ | Quotes carry one best bid/ask (NBBO), not depth levels; `NotSupportedError` by design |
| `fetch_fundamentals` | ❌ | No fundamentals endpoint (assets rows lack currency/`as_of`/metrics, news is content not reference); `NotSupportedError` by design |
| `search_instruments` | ✅ | Fresh assets-master fetch per call; substring on symbol **and** name; combinable `asset_class`/`instrument_type`/`quote`/`exchange` filters |
| `fetch_ohlcv_batch` | ✅ | Inherited thread-pool fan-out (adapter is `concurrency_model="thread"`) |

## Timeframes

`1m, 5m, 15m, 30m, 1h, 2h, 4h, 8h, 12h, 1d, 1w, 1mo` → Alpaca `TimeFrame`
(`1Min…1Month`). Unknown timeframe → `InvalidTimeframeError`. A non-native
target would be derived by resampling (design doc sec 8); all twelve are
native today so the fallback sleeps.

## Asset classes

US equities, US options (reference only), crypto spot — the only classes in
Alpaca's taxonomy (`us_equity`/`us_option`/`crypto`). No forex, futures, or
indices at any tier. Options historical bars need a dedicated endpoint and
are not implemented.

## Cost tier notes

- Market data needs a key pair (`APCA_API_KEY_ID` / `APCA_API_SECRET_KEY`);
  explicit `api_key=`/`api_secret=` wins over env/`.env`.
- `iex` is the only feed without a paid plan; `sip` (and gated tick depth)
  returns HTTP 403 → `PaidTierRequiredError`, never faked data.
- Token-bucket gated; 429s honor `Retry-After` with backoff,
  exhaustion → `RetriesExhaustedError`.

## Sessions

Stock bars carry `session="regular"`. Crypto trades 24/7, so crypto frames
have no `session` column at all — an all-`None` column would imply a session
boundary that does not exist. Requesting `columns=["session"]` for crypto
raises `ValueError`.

Equity request boundaries follow the XNYS calendar: holidays and half days
come from `exchange_calendars` when the `calendar` extra is installed, and the
built-in weekend calendar otherwise. Alpaca labels daily and larger bars by
calendar date, so the adapter moves those timestamps to the session open
(for example 05:00 UTC becomes 14:30 UTC on a regular January day) before the
fetch contract runs. Crypto uses the 24/7 calendar and keeps UTC-midnight
period boundaries.

## Paper vs live

Paper mode affects order execution, never market data. Bars are identical on
both. Reference reads (assets) need the host that matches your key pair:
paper keys authenticate only on the paper host and live keys only on the live
host. The default is `AlpacaConfig(paper=True)`, so set `paper=False` in
`AlpacaConfig` when using live keys. This library never submits orders.

## Restatement

Crypto closed bars are effectively never revised; equities are restated
(corrections, corporate actions). No local cache is kept — every fetch
returns the provider's latest truth. `adjustment="raw"` (default) requests
unadjusted equity bars.

The frame records the data semantics, so downstream code can tell what it
received: `df.attrs["volume_type"]` (`"shares"` for equities, `"base"` for
crypto), `df.attrs["price_adjustment"]` (`"raw"` or `"split_and_dividend"`),
and `df.attrs["data_tier"]` (the `feed`, e.g. `"iex"`).

## Output format

pandas (default), polars, or arrow — globally via `Config(output_format=…)`
or per call via `output_format=…`.
