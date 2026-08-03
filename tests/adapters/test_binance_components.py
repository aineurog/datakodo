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
from datakodo.adapters.binance.mapper import map_ohlcv, map_trades
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


# --- 3. REST helpers ----------------------------------------------------------


def test_to_millis_aware():
    dt = datetime(2024, 1, 1, tzinfo=UTC)
    assert _to_millis(dt) == int(dt.timestamp() * 1000)


def test_to_millis_naive_assumed_utc():
    dt = datetime(2024, 1, 1)
    assert _to_millis(dt) == _to_millis(datetime(2024, 1, 1, tzinfo=UTC))


def test_klines_weight_spot():
    assert BinanceREST._klines_weight(100, "spot") == 2
    assert BinanceREST._klines_weight(600, "spot") == 5


def test_klines_weight_futures_flat():
    assert BinanceREST._klines_weight(1000, "futures") == 1


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
        check_capability(BinanceAdapter(), "supports_fundamentals")


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
