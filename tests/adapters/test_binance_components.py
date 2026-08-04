"""Simple per-component tests for the Binance adapter.

Each test targets one component in isolation with mocked/offline data —
no live API calls, no keys. Covers:

  core.config.Config        binance_* settings, env override, .env
  mapper.map_ohlcv          raw klines -> canonical OHLCV DataFrame
  mapper.map_trades         raw trade msg -> canonical Trade
  rest._to_millis           datetime -> epoch ms
  rest._klines_weight       request weight per market/limit
  rest._translate           BinanceAPIException -> DataKodo exception
  rest.klines               endpoint routing (spot vs futures, mocked)
  ws.BinanceWS              construction + config plumbing
  adapter.instrument        spot vs perpetual Instrument
  adapter capabilities      flags + check_capability
  adapter.fetch_ohlcv       end-to-end with mocked REST
  ops.pagination.paginate   chunking + stitching + dedupe
  ops.validation.validate   quality checks (pass and fail)
  storage.cache             build_cache_key / is_bar_closed / compute_expiry
  storage.parquet           write / read / exists round-trip

Run:
    python -m pytest tests/adapters/test_binance_components.py -v
"""

from datetime import UTC, datetime

import pandas as pd
import pytest

from datakodo.adapters.binance.adapter import BinanceAdapter
from datakodo.adapters.binance.mapper import (
    map_fundamentals,
    map_ohlcv,
    map_trades,
)
from datakodo.adapters.binance.rest import BinanceREST, _to_millis
from datakodo.adapters.binance.ws import BinanceWS
from datakodo.core.config import Config
from datakodo.core.enums import AssetClass, InstrumentType, Timeframe
from datakodo.core.exceptions import (
    AuthenticationError,
    ConnectionError,
    NotSupportedError,
    ProviderError,
    RateLimitError,
    SymbolNotFoundError,
)
from datakodo.core.interfaces import check_capability
from datakodo.ops.pagination import paginate
from datakodo.ops.resample import pick_source_timeframe
from datakodo.ops.validation import validate_ohlcv
from datakodo.storage.cache import build_cache_key, compute_expiry, is_bar_closed
from datakodo.storage.parquet import ParquetBackend

# --- fixtures ---------------------------------------------------------------

RAW_KLINE = [
    1704067200000,  # open_time (ms)
    "40000.0",  # open
    "41000.0",  # high
    "39000.0",  # low
    "40500.0",  # close
    "12.5",  # volume
    1704067260000,  # close_time
    "500000.0",  # quote volume
    100,  # trades
    "6.25",  # taker buy base
    "250000.0",  # taker buy quote
    0,  # ignore
]

RAW_KLINES = [RAW_KLINE]


@pytest.fixture(autouse=True)
def _clear_datakodo_env(monkeypatch):
    """Make tests hermetic: drop real DATAKODO_* env vars.

    Without this, a developer's real API keys set in the environment leak
    into ``Config`` defaults and break the default-value assertions.
    """
    keys = list(Config.model_fields) + [
        "DATAKODO_BINANCE_API_KEY",
        "DATAKODO_BINANCE_API_SECRET",
    ]
    for key in keys:
        monkeypatch.delenv("DATAKODO_" + key.upper(), raising=False)


def _make_klines(n: int, start_ms: int = 1704067200000, step_ms: int = 3600_000) -> list:
    """Build *n* raw Binance klines with distinct, increasing open times."""
    return [
        [
            start_ms + i * step_ms,  # open_time
            "40000.0",
            "41000.0",
            "39000.0",
            "40500.0",
            "12.5",
            start_ms + i * step_ms + 60_000,
            "500000.0",
            100,
            "6.25",
            "250000.0",
            0,
        ]
        for i in range(n)
    ]


# --- 1. Config ---------------------------------------------------------------


def test_config_defaults():
    cfg = Config(_env_file=None)
    assert cfg.binance_api_key == ""
    assert cfg.binance_market_type == "spot"
    assert cfg.binance_tld == "com"
    assert cfg.binance_timeout == 10.0


def test_config_env_override(monkeypatch):
    monkeypatch.setenv("DATAKODO_BINANCE_API_KEY", "abc")
    monkeypatch.setenv("DATAKODO_BINANCE_TESTNET", "true")
    cfg = Config()
    assert cfg.binance_api_key == "abc"
    assert cfg.binance_testnet is True


def test_config_explicit_kwargs_win():
    cfg = Config(binance_market_type="futures", binance_tld="us")
    assert cfg.binance_market_type == "futures"
    assert cfg.binance_tld == "us"


# --- 2. Mapper ----------------------------------------------------------------


def test_map_ohlcv_columns():
    df = map_ohlcv(RAW_KLINES)
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume", "session"]
    assert df["timestamp"].iloc[0].tz is not None
    assert df["timestamp"].iloc[0] == pd.Timestamp(1704067200000, unit="ms", tz="UTC")
    assert df["open"].iloc[0] == 40000.0
    assert df["session"].iloc[0] == "n/a"


def test_map_ohlcv_empty():
    df = map_ohlcv([])
    assert df.empty
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume", "session"]


def test_map_trades_buy():
    trade = map_trades({"T": 1704067200000, "p": "50000.0", "q": "0.1", "m": True})
    assert trade.side == "buy"
    assert trade.price == 50000.0
    assert trade.size == 0.1
    assert trade.timestamp.tz is not None


def test_map_trades_sell_default():
    trade = map_trades({"T": 1704067200000, "p": "50001.0", "q": "0.2", "m": False})
    assert trade.side == "sell"


def test_map_fundamentals_ticker_and_info():
    ticker = {
        "symbol": "BTCUSDT",
        "lastPrice": "63758.0",
        "priceChange": "123.0",
        "openPrice": "63000.0",
        "highPrice": "64000.0",
        "lowPrice": "62000.0",
        "volume": "1234.5",
        "quoteVolume": "77000000.0",
        "closeTime": 1704067200000,
    }
    info = {
        "symbol": "BTCUSDT",
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "status": "TRADING",
        "isSpotTradingAllowed": True,
        "isMarginTradingAllowed": True,
        "permissions": ["SPOT", "MARGIN"],
    }
    f = map_fundamentals(ticker, info)
    assert f.symbol == "BTCUSDT"
    assert f.latest_price == 63758.0
    assert f.volume_24h == 1234.5
    assert f.high_24h == 64000.0
    assert f.currency == "USDT"
    assert f.exchange == "Binance"
    assert f.timestamp is not None
    assert f.crypto is not None
    assert f.crypto.base_asset == "BTC"
    assert f.crypto.status == "TRADING"
    assert f.crypto.permissions == ["SPOT", "MARGIN"]


def test_map_fundamentals_falls_back_to_symbol_parsing():
    ticker = {"symbol": "ETHUSDT", "lastPrice": "3000.0"}
    f = map_fundamentals(ticker)  # no info block
    assert f.currency == "USDT"
    assert f.crypto.base_asset == "ETH"
    assert f.crypto.quote_asset == "USDT"
    assert f.crypto.permissions == ["SPOT"]
    assert f.crypto.status == "TRADING"


# --- 3. REST helpers ----------------------------------------------------------


def test_to_millis_aware():
    dt = datetime(2024, 1, 1, tzinfo=UTC)
    assert _to_millis(dt) == int(dt.timestamp() * 1000)


def test_to_millis_naive_assumed_utc():
    dt = datetime(2024, 1, 1)
    assert _to_millis(dt) == _to_millis(datetime(2024, 1, 1, tzinfo=UTC))


def test_klines_weight_spot():
    # Spot klines weight is flat 2 (official Binance docs, 2026).
    assert BinanceREST._klines_weight(100, "spot") == 2
    assert BinanceREST._klines_weight(600, "spot") == 2
    assert BinanceREST._klines_weight(1000, "spot") == 2


def test_klines_weight_futures():
    # USD-M futures klines: 1 for [1,100), 2 for [100,500), 5 for [500,1000], 10 above.
    assert BinanceREST._klines_weight(50, "futures") == 1
    assert BinanceREST._klines_weight(100, "futures") == 2
    assert BinanceREST._klines_weight(500, "futures") == 5
    assert BinanceREST._klines_weight(1000, "futures") == 10


def _make_api_exc(code: int, status: int, message: str):
    import json

    from binance.exceptions import BinanceAPIException

    text = json.dumps({"code": code, "msg": message})
    return BinanceAPIException(
        response=type("R", (), {"text": text})(),
        status_code=status,
        text=text,
    )


def test_translate_symbol_not_found():
    with pytest.raises(SymbolNotFoundError):
        raise BinanceREST._translate(_make_api_exc(-1121, 400, "Invalid symbol."))


def test_translate_auth():
    with pytest.raises(AuthenticationError):
        raise BinanceREST._translate(_make_api_exc(-2015, 400, "Invalid API-key"))


def test_translate_rate_limit():
    with pytest.raises(RateLimitError):
        raise BinanceREST._translate(_make_api_exc(-1003, 429, "Too many requests"))


def test_translate_connection():
    with pytest.raises(ConnectionError):
        raise BinanceREST._translate(_make_api_exc(-1001, 400, "disconnected"))


def test_translate_provider_fallback():
    with pytest.raises(ProviderError):
        raise BinanceREST._translate(_make_api_exc(-9999, 400, "weird error"))


def test_rest_klines_routes_to_futures():
    rest = BinanceREST()
    calls = {}

    class _Futures:
        def __call__(self, **kwargs):
            calls["futures"] = kwargs
            return RAW_KLINES

    class _Spot:
        def __call__(self, **kwargs):
            calls["spot"] = kwargs
            return RAW_KLINES

    rest._client = type("FakeClient", (), {"futures_klines": _Futures(), "get_klines": _Spot()})()

    assert rest.klines("BTCUSDT", "1h", market_type="futures") == RAW_KLINES
    assert "futures" in calls
    assert rest.klines("BTCUSDT", "1h", market_type="spot") == RAW_KLINES
    assert "spot" in calls


# --- 4. WS ---------------------------------------------------------------------


def test_ws_config_plumbing():
    ws = BinanceWS(config=Config(binance_tld="us", binance_testnet=True))
    assert ws._config.binance_tld == "us"
    assert ws._config.binance_testnet is True


def test_ws_stream_names(monkeypatch):
    calls = []

    class FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def recv(self):
            return {"e": "x"}  # single message; test drains one then closes

    class FakeManager:
        def aggtrade_socket(self, symbol):
            calls.append(("spot", "trade"))
            return FakeStream()

        def depth_socket(self, symbol):
            calls.append(("spot", "book"))
            return FakeStream()

        def aggtrade_futures_socket(self, symbol):
            calls.append(("futures", "trade"))
            return FakeStream()

        def futures_depth_socket(self, symbol):
            calls.append(("futures", "book"))
            return FakeStream()

    class FakeClient:
        async def close_connection(self):
            return None

    async def _fake_create(*a, **kw):
        return FakeClient()

    ws = BinanceWS()
    monkeypatch.setattr(ws, "_client", _fake_create)
    monkeypatch.setattr(
        "datakodo.adapters.binance.ws.BinanceSocketManager", lambda c: FakeManager()
    )

    async def _one(generator):
        results = []
        async for msg in generator:
            results.append(msg)
            break
        await generator.aclose()
        return results

    import asyncio

    assert asyncio.run(_one(ws.trade_stream("BTCUSDT", market_type="spot"))) == [{"e": "x"}]
    asyncio.run(_one(ws.orderbook_stream("BTCUSDT", market_type="spot")))
    asyncio.run(_one(ws.trade_stream("BTCUSDT", market_type="futures")))
    asyncio.run(_one(ws.orderbook_stream("BTCUSDT", market_type="futures")))

    assert calls == [
        ("spot", "trade"),
        ("spot", "book"),
        ("futures", "trade"),
        ("futures", "book"),
    ]


# --- 5. Adapter -----------------------------------------------------------------


def test_adapter_instrument_spot():
    inst = BinanceAdapter().instrument("BTCUSDT", market_type="spot")
    assert inst.asset_class == AssetClass.CRYPTO
    assert inst.instrument_type == InstrumentType.SPOT
    assert inst.currency == "USDT"


def test_adapter_instrument_futures_perpetual():
    inst = BinanceAdapter().instrument("BTCUSDT", market_type="futures")
    assert inst.instrument_type == InstrumentType.PERPETUAL
    assert inst.crypto_perpetual is not None


def test_adapter_capabilities():
    adapter = BinanceAdapter()
    assert adapter.supports_ohlcv is True
    assert adapter.supports_ticks is True
    assert adapter.supports_streaming_orderbook is True
    assert adapter.requires_paid_tier is False
    check_capability(adapter, "supports_ohlcv")  # must not raise


def test_adapter_check_capability_rejects_unsupported():
    with pytest.raises(NotSupportedError):
        check_capability(BinanceAdapter(), "supports_symbol_search")


def test_adapter_fetch_ohlcv_mocked(monkeypatch, tmp_path):
    adapter = BinanceAdapter(storage=ParquetBackend(base_dir=str(tmp_path)))
    raw = _make_klines(3)
    monkeypatch.setattr(adapter._rest, "klines", lambda *a, **k: raw)
    df = adapter.fetch_ohlcv(
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
        market_type="spot",
    )
    assert len(df) == 3
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume", "session"]
    assert df["timestamp"].is_monotonic_increasing


def test_fetch_ohlcv_output_format_polars(monkeypatch, tmp_path):
    adapter = BinanceAdapter(storage=ParquetBackend(base_dir=str(tmp_path)))
    monkeypatch.setattr(adapter._rest, "klines", lambda *a, **k: _make_klines(2))
    out = adapter.fetch_ohlcv(
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
        market_type="spot",
        output_format="polars",
    )
    assert type(out).__name__ == "DataFrame"  # polars.DataFrame
    assert out.height == 2


def test_fetch_ohlcv_output_format_arrow(monkeypatch, tmp_path):
    adapter = BinanceAdapter(storage=ParquetBackend(base_dir=str(tmp_path)))
    monkeypatch.setattr(adapter._rest, "klines", lambda *a, **k: _make_klines(2))
    out = adapter.fetch_ohlcv(
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
        market_type="spot",
        output_format="arrow",
    )
    assert type(out).__name__ == "Table"  # pyarrow.Table
    assert out.num_rows == 2


def test_fetch_ohlcv_batch_returns_mapping(monkeypatch, tmp_path):
    adapter = BinanceAdapter(storage=ParquetBackend(base_dir=str(tmp_path)))
    monkeypatch.setattr(adapter._rest, "klines", lambda *a, **k: _make_klines(2))
    result = adapter.fetch_ohlcv_batch(
        ["BTCUSDT", "ETHUSDT"],
        "1h",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
        market_type="spot",
        persist=False,
    )
    assert set(result) == {"BTCUSDT", "ETHUSDT"}
    for df in result.values():
        assert len(df) == 2
        assert list(df.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "session",
        ]


def test_fetch_ohlcv_batch_combine(monkeypatch, tmp_path):
    adapter = BinanceAdapter(storage=ParquetBackend(base_dir=str(tmp_path)))
    monkeypatch.setattr(adapter._rest, "klines", lambda *a, **k: _make_klines(2))
    combined = adapter.fetch_ohlcv_batch(
        ["BTCUSDT", "ETHUSDT"],
        "1h",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
        combine=True,
        market_type="spot",
        persist=False,
    )
    assert len(combined) == 4
    assert "symbol" in combined.columns
    assert combined["symbol"].tolist() == ["BTCUSDT", "BTCUSDT", "ETHUSDT", "ETHUSDT"]


def test_fetch_ohlcv_batch_empty_symbols_raises():
    adapter = BinanceAdapter()
    with pytest.raises(ValueError, match="At least one symbol"):
        adapter.fetch_ohlcv_batch(
            [], "1h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )


def test_fetch_ohlcv_batch_output_format(monkeypatch, tmp_path):
    adapter = BinanceAdapter(storage=ParquetBackend(base_dir=str(tmp_path)))
    monkeypatch.setattr(adapter._rest, "klines", lambda *a, **k: _make_klines(2))
    result = adapter.fetch_ohlcv_batch(
        ["BTCUSDT", "ETHUSDT"],
        "1h",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
        output_format="polars",
        market_type="spot",
        persist=False,
    )
    for frame in result.values():
        assert type(frame).__name__ == "DataFrame"  # polars.DataFrame
        assert frame.height == 2


def test_fetch_ohlcv_batch_respects_max_workers(monkeypatch, tmp_path):
    adapter = BinanceAdapter(storage=ParquetBackend(base_dir=str(tmp_path)))
    monkeypatch.setattr(adapter._rest, "klines", lambda *a, **k: _make_klines(1))
    result = adapter.fetch_ohlcv_batch(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "1h",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
        max_workers=2,
        market_type="spot",
        persist=False,
    )
    assert set(result) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}


# --- 5c. Fundamentals ---------------------------------------------------------------


def _fake_ticker(symbol="BTCUSDT"):
    return {
        "symbol": symbol,
        "lastPrice": "63758.0",
        "priceChange": "123.0",
        "openPrice": "63000.0",
        "highPrice": "64000.0",
        "lowPrice": "62000.0",
        "volume": "1234.5",
        "quoteVolume": "77000000.0",
        "closeTime": 1704067200000,
    }


def _fake_info(symbol="BTCUSDT"):
    return {
        "symbol": symbol,
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "status": "TRADING",
        "isSpotTradingAllowed": True,
        "isMarginTradingAllowed": True,
        "permissions": ["SPOT"],
    }


def test_adapter_fetch_fundamentals_mocked(monkeypatch):
    adapter = BinanceAdapter()
    monkeypatch.setattr(adapter._rest, "ticker_24h", lambda *a, **k: _fake_ticker())
    monkeypatch.setattr(adapter._rest, "exchange_info", lambda *a, **k: _fake_info())

    f = adapter.fetch_fundamentals("BTCUSDT", market_type="spot")
    assert f.symbol == "BTCUSDT"
    assert f.latest_price == 63758.0
    assert f.crypto.status == "TRADING"


def test_adapter_supports_fundamentals():
    adapter = BinanceAdapter()
    assert adapter.supports_fundamentals is True
    check_capability(adapter, "supports_fundamentals")  # must not raise


# --- 5d. Client facade ---------------------------------------------------------------


def test_client_binance_dispatch(monkeypatch, tmp_path):
    from datakodo import Client

    client = Client("binance", config=Config(cache_enabled=False))
    assert repr(client).startswith("Client(provider=")
    assert client.adapter is not None

    monkeypatch.setattr(client.adapter._rest, "klines", lambda *a, **k: _make_klines(2))
    df = client.fetch_ohlcv(
        "BTCUSDT", "1h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
    )
    assert len(df) == 2


def test_client_unknown_provider_raises():
    from datakodo import Client

    with pytest.raises(ValueError, match="Unknown provider"):
        Client("alpaca")  # not registered yet


# --- 5b. Resampling ---------------------------------------------------------------


def _resample_kline(
    open_ms: int, open_p: str, high_p: str, low_p: str, close_p: str, volume: str
) -> list:
    """Raw Binance kline row (12 fields) with prices/volume as strings."""
    return [
        open_ms,
        open_p,
        high_p,
        low_p,
        close_p,
        volume,
        open_ms + 3_599_999,
        volume,
        100,
        "0",
        "0",
        "0",
    ]


class _Restricted(BinanceAdapter):
    """Binance adapter pretending to offer only 1m and 1h natively."""

    native_timeframes = (Timeframe.M1, Timeframe.H1)


def test_pick_source_timeframe_returns_largest_smaller():
    native = (Timeframe.M1, Timeframe.H1, Timeframe.D1)
    assert pick_source_timeframe(Timeframe.H4, native) == Timeframe.H1
    assert pick_source_timeframe(Timeframe.H4, native) != Timeframe.D1


def test_pick_source_timeframe_requires_smaller_source():
    with pytest.raises(ValueError, match="no native timeframe"):
        pick_source_timeframe(Timeframe.M1, (Timeframe.H1,))


def test_fetch_ohlcv_resamples_non_native_timeframe(monkeypatch):
    """4h requested, only 1h native -> fetch 1h and resample to one 4h bar."""
    adapter = _Restricted()
    raw = [
        _resample_kline(1704067200000, "100", "110", "90", "105", "100"),  # 00:00
        _resample_kline(1704070800000, "105", "120", "100", "115", "200"),  # 01:00
        _resample_kline(1704074400000, "115", "130", "110", "125", "300"),  # 02:00
        _resample_kline(1704078000000, "125", "140", "120", "135", "400"),  # 03:00
    ]
    monkeypatch.setattr(adapter._rest, "klines", lambda *a, **k: raw)

    df = adapter.fetch_ohlcv(
        "BTCUSDT",
        "4h",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 1, 4, tzinfo=UTC),
        market_type="spot",
        persist=False,
    )
    assert len(df) == 1
    assert df.loc[0, "open"] == 100.0  # first source open
    assert df.loc[0, "high"] == 140.0  # max high
    assert df.loc[0, "low"] == 90.0  # min low
    assert df.loc[0, "close"] == 135.0  # last source close
    assert df.loc[0, "volume"] == 1000.0


def test_fetch_ohlcv_native_timeframe_is_not_resampled(monkeypatch):
    """A natively offered timeframe is fetched directly, no resampling."""
    adapter = _Restricted()
    captured: dict[str, str] = {}

    def _fake(
        symbol: str, interval: str, start: object, end: object, market_type: str = "spot"
    ) -> list:
        captured["interval"] = interval
        return [_resample_kline(1704067200000, "100", "110", "90", "105", "100")]

    monkeypatch.setattr(adapter._rest, "klines", _fake)
    df = adapter.fetch_ohlcv(
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 1, 2, tzinfo=UTC),
        market_type="spot",
        persist=False,
    )
    assert captured["interval"] == "1h"
    assert len(df) == 1


def test_fetch_ohlcv_no_native_source_raises():
    """1m cannot be derived from a native set starting at 1h (downsampling)."""

    class _NoSmall(_Restricted):
        native_timeframes = (Timeframe.H1,)

    adapter = _NoSmall()
    with pytest.raises(ValueError, match="no native timeframe"):
        adapter.fetch_ohlcv(
            "BTCUSDT",
            "1m",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 1, 1, 30, tzinfo=UTC),
            market_type="spot",
            persist=False,
        )


def test_fetch_ohlcv_binance_native_includes_all_timeframes():
    """Real Binance adapter: every canonical timeframe is native."""
    adapter = BinanceAdapter()
    assert tuple(Timeframe) == adapter.native_timeframes


# --- 6. Pagination ---------------------------------------------------------------


def _fake_fetch(rows):
    def _fn(symbol, start, end):
        return map_ohlcv(rows)

    return _fn


def test_paginate_stitches_and_dedupes():
    rows = _make_klines(5)
    df = paginate(
        _fake_fetch(rows),
        "BTCUSDT",
        Timeframe.H1,
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
        max_per_request=2,
    )
    assert len(df) == 5
    assert df["timestamp"].is_monotonic_increasing
    assert not df["timestamp"].duplicated().any()


def test_paginate_rejects_bad_range():
    with pytest.raises(ValueError):
        paginate(
            _fake_fetch([]),
            "BTCUSDT",
            Timeframe.H1,
            datetime(2024, 1, 2, tzinfo=UTC),
            datetime(2024, 1, 1, tzinfo=UTC),
        )


# --- 7. Validation ---------------------------------------------------------------


def _ohlcv_df(timestamps):
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [1.0] * len(timestamps),
            "high": [2.0] * len(timestamps),
            "low": [0.5] * len(timestamps),
            "close": [1.5] * len(timestamps),
            "volume": [10.0] * len(timestamps),
            "session": ["n/a"] * len(timestamps),
        }
    )


def test_validate_ohlcv_ok():
    df = _ohlcv_df(
        [pd.Timestamp(1704067200000, unit="ms", tz="UTC") + pd.Timedelta(hours=i) for i in range(3)]
    )
    validate_ohlcv(df)  # must not raise


def test_validate_ohlcv_empty_raises():
    with pytest.raises(ValueError):
        validate_ohlcv(pd.DataFrame())


def test_validate_ohlcv_high_below_low_raises():
    df = _ohlcv_df([pd.Timestamp(1704067200000, unit="ms", tz="UTC")])
    df.loc[0, "high"] = 0.1
    with pytest.raises(ValueError):
        validate_ohlcv(df)


def test_validate_ohlcv_duplicate_timestamps_raises():
    ts = pd.Timestamp(1704067200000, unit="ms", tz="UTC")
    df = _ohlcv_df([ts, ts])
    with pytest.raises(ValueError):
        validate_ohlcv(df)


# --- 8. Cache --------------------------------------------------------------------


def test_build_cache_key():
    key = build_cache_key("binance-spot", "BTCUSDT", "1h", ("2024-01-01", "2024-01-02"))
    assert key == "binance-spot/BTCUSDT/1h/2024-01-01_2024-01-02"


def test_is_bar_closed():
    assert is_bar_closed(datetime(2020, 1, 1, tzinfo=UTC), "1d") is True
    assert is_bar_closed(datetime.now(UTC) + pd.Timedelta(hours=1), "1d") is False


def test_compute_expiry():
    assert compute_expiry("1m") <= datetime.now(UTC) + pd.Timedelta(minutes=6)
    assert compute_expiry("1d") > datetime.now(UTC) + pd.Timedelta(days=365)


# --- 9. Parquet storage -------------------------------------------------------------


def test_parquet_roundtrip(tmp_path):
    store = ParquetBackend(base_dir=str(tmp_path))
    df = _ohlcv_df([pd.Timestamp(1704067200000, unit="ms", tz="UTC")])
    key = "binance-spot/BTCUSDT/1h/2024-01-01_2024-01-02"

    assert store.exists(key) is False
    store.write(key, df)
    assert store.exists(key) is True
    out = pd.DataFrame(store.read(key))
    assert list(out.columns) == list(df.columns)
    assert len(out) == len(df)


def test_parquet_read_missing_raises(tmp_path):
    store = ParquetBackend(base_dir=str(tmp_path))
    with pytest.raises(KeyError):
        store.read("does/not/exist")


def test_parquet_write_rejects_non_dataframe(tmp_path):
    store = ParquetBackend(base_dir=str(tmp_path))
    with pytest.raises(TypeError):
        store.write("some/key", "not a dataframe")


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
