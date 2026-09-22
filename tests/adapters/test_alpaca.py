"""Alpaca adapter tests — step 1 skeleton + step 2 transport (offline only)."""

import asyncio
import json
import time
from datetime import UTC, datetime
from importlib.metadata import entry_points
from types import SimpleNamespace

import pytest
from alpaca.common.exceptions import APIError
from alpaca.data import Bar
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.trading.enums import AssetClass as AlpacaAssetClass
from alpaca.trading.enums import AssetExchange
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout

from datakodo.adapters.alpaca.adapter import AlpacaAdapter
from datakodo.adapters.alpaca.config import AlpacaConfig
from datakodo.adapters.alpaca.mapper import map_instrument
from datakodo.adapters.alpaca.rest import AlpacaREST, alpaca_resolution
from datakodo.core.config import Config
from datakodo.core.enums import AssetClass, InstrumentType, Timeframe
from datakodo.core.exceptions import (
    AuthenticationError,
    ConnectionError,
    DataNotAvailableError,
    DataValidationError,
    InvalidTimeframeError,
    NotSupportedError,
    PaidTierRequiredError,
    RetriesExhaustedError,
    SymbolNotFoundError,
    TimeoutError,
)
from datakodo.ratelimit.limiter import TokenBucket

_ENV_VARS = (
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "APCA_DATA_BASE_URL",
    "APCA_FEED",
    "APCA_RATE_LIMIT_RATE",
    "APCA_RATE_LIMIT_BURST",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Isolate tests from the developer's real environment and .env file."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Ignore the repo .env entirely: a developer key there must not leak in.
    monkeypatch.setattr(
        AlpacaConfig,
        "model_config",
        {**AlpacaConfig.model_config, "env_file": None},
    )


class TestAlpacaSkeleton:
    def test_adapter_capabilities(self):
        adapter = AlpacaAdapter()
        assert adapter.supports_ohlcv is True
        assert adapter.supports_ticks is True
        assert adapter.supports_orderbook_snapshot is False
        assert adapter.supports_streaming_orderbook is False
        assert adapter.supports_streaming_ticks is True
        assert adapter.supports_fundamentals is False
        assert adapter.concurrency_model == "thread"

    def test_empty_init_is_lazy(self):
        """Missing keys construct fine; auth fails only when a call needs it."""
        adapter = AlpacaAdapter()
        assert adapter._alpaca.api_key == ""
        assert adapter._alpaca.api_secret == ""

    def test_config_defaults(self):
        config = AlpacaConfig()
        assert config.data_base_url == "https://data.alpaca.markets"
        assert config.feed == "iex"

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("APCA_API_KEY_ID", "env-key")
        monkeypatch.setenv("APCA_API_SECRET_KEY", "env-secret")
        adapter = AlpacaAdapter()
        assert adapter._alpaca.api_key == "env-key"
        assert adapter._alpaca.api_secret == "env-secret"

    def test_explicit_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("APCA_API_KEY_ID", "env-key")
        monkeypatch.setenv("APCA_API_SECRET_KEY", "env-secret")
        adapter = AlpacaAdapter(api_key="explicit-key", api_secret="explicit-secret")
        assert adapter._alpaca.api_key == "explicit-key"
        assert adapter._alpaca.api_secret == "explicit-secret"

    def test_alias_fallback(self, monkeypatch):
        """ALPACA_* spellings work when APCA_* spellings are absent."""
        monkeypatch.setenv("ALPACA_API_KEY", "alias-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "alias-secret")
        adapter = AlpacaAdapter()
        assert adapter._alpaca.api_key == "alias-key"
        assert adapter._alpaca.api_secret == "alias-secret"

    def test_primary_beats_alias(self, monkeypatch):
        monkeypatch.setenv("APCA_API_KEY_ID", "primary-key")
        monkeypatch.setenv("ALPACA_API_KEY", "alias-key")
        monkeypatch.setenv("APCA_API_SECRET_KEY", "primary-secret")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "alias-secret")
        adapter = AlpacaAdapter()
        assert adapter._alpaca.api_key == "primary-key"
        assert adapter._alpaca.api_secret == "primary-secret"

    def test_explicit_config_object(self):
        config = AlpacaConfig(api_key="cfg-key", api_secret="cfg-secret")
        adapter = AlpacaAdapter(alpaca_config=config)
        assert adapter._alpaca.api_key == "cfg-key"
        assert adapter._alpaca.api_secret == "cfg-secret"


class TestAlpacaNotYetImplemented:
    def test_unsupported_surfaces_raise_not_supported(self):
        adapter = AlpacaAdapter(api_key="k", api_secret="s")
        with pytest.raises(NotSupportedError):
            adapter.fetch_ticks("AAPL")
        with pytest.raises(NotSupportedError):
            adapter.fetch_orderbook_snapshot("AAPL")
        with pytest.raises(NotSupportedError):
            adapter.fetch_fundamentals("AAPL")

    def test_streaming_raises_not_supported(self):
        adapter = AlpacaAdapter(api_key="k", api_secret="s")

        async def _drain(gen):
            async for _ in gen:
                pass

        with pytest.raises(NotSupportedError):
            asyncio.run(_drain(adapter.stream_trades("AAPL")))
        with pytest.raises(NotSupportedError):
            asyncio.run(_drain(adapter.stream_orderbook("AAPL")))


class TestAlpacaPackaging:
    def test_entry_point_registered(self):
        eps = entry_points()
        group = eps.select(group="datakodo.adapters") if hasattr(eps, "select") else []
        names = {ep.name for ep in group}
        assert "alpaca" in names

    def test_client_resolves_alpaca(self):
        from datakodo.client import Client

        assert "alpaca" in Client.available_providers()
        client = Client("alpaca", api_key="k", api_secret="s")
        assert isinstance(client.adapter, AlpacaAdapter)


def _api_error(status, message="boom", headers=None):
    """Build an SDK APIError without any network."""
    body = json.dumps({"code": status, "message": message})
    http_error = SimpleNamespace(
        response=SimpleNamespace(status_code=status, headers=headers or {})
    )
    return APIError(body, http_error)


class _FakeSDKClient:
    """Scripted stand-in for StockHistoricalDataClient."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def raw(self, *args, **kwargs):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _rest_with_fake(monkeypatch, script, **kwargs):
    """AlpacaREST with keys set and the SDK client replaced by a fake."""
    kwargs.setdefault("alpaca_config", AlpacaConfig(api_key="k", api_secret="s"))
    rest = AlpacaREST(**kwargs)
    fake = _FakeSDKClient(script)
    monkeypatch.setattr(AlpacaREST, "_ensure_client", lambda self, kind="stock": fake)
    return rest, fake


def _no_sleep(monkeypatch):
    """Record backoff waits instead of sleeping."""
    waits = []
    monkeypatch.setattr(time, "sleep", waits.append)
    return waits


class TestAlpacaTransport:
    def test_call_success(self, monkeypatch):
        rest, fake = _rest_with_fake(monkeypatch, ["ok"])
        assert rest._call("raw") == "ok"
        assert fake.calls == 1

    def test_missing_keys_raise_auth(self):
        with pytest.raises(AuthenticationError):
            AlpacaREST()._call("raw")

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (400, DataValidationError),
            (422, DataValidationError),
            (401, AuthenticationError),
            (403, PaidTierRequiredError),
            (404, SymbolNotFoundError),
        ],
    )
    def test_status_map_no_retry(self, monkeypatch, status, expected):
        rest, fake = _rest_with_fake(monkeypatch, [_api_error(status)])
        with pytest.raises(expected):
            rest._call("raw")
        assert fake.calls == 1

    def test_429_retry_then_success(self, monkeypatch):
        rest, fake = _rest_with_fake(monkeypatch, [_api_error(429), "ok"])
        waits = _no_sleep(monkeypatch)
        assert rest._call("raw") == "ok"
        assert fake.calls == 2
        assert len(waits) == 1

    def test_429_exhausted_reports_retry_after(self, monkeypatch):
        rest, fake = _rest_with_fake(
            monkeypatch,
            [
                _api_error(429, headers={"Retry-After": "7"}),
                _api_error(429, headers={"Retry-After": "7"}),
            ],
            config=Config(max_retries=1),
        )
        waits = _no_sleep(monkeypatch)
        with pytest.raises(RetriesExhaustedError) as excinfo:
            rest._call("raw")
        assert isinstance(excinfo.value.__cause__, Exception)
        assert getattr(excinfo.value.__cause__, "retry_after", None) == 7.0
        assert waits == [7.0]
        assert fake.calls == 2

    def test_5xx_exhausted(self, monkeypatch):
        rest, fake = _rest_with_fake(monkeypatch, [_api_error(503)], config=Config(max_retries=0))
        with pytest.raises(RetriesExhaustedError):
            rest._call("raw")
        assert fake.calls == 1

    def test_timeout_vs_connection_distinct(self, monkeypatch):
        rest, _ = _rest_with_fake(
            monkeypatch, [RequestsTimeout("slow")], config=Config(max_retries=0)
        )
        with pytest.raises(TimeoutError):
            rest._call("raw")

        rest, _ = _rest_with_fake(
            monkeypatch,
            [RequestsConnectionError("down")],
            config=Config(max_retries=0),
        )
        with pytest.raises(ConnectionError):
            rest._call("raw")

    def test_local_bucket_exhausted(self, monkeypatch):
        rest, fake = _rest_with_fake(monkeypatch, ["ok"], config=Config(max_retries=0))
        rest._limiter = TokenBucket(rate=0.0001, burst=1)
        assert rest._call("raw") == "ok"
        with pytest.raises(RetriesExhaustedError, match="local rate limit"):
            rest._call("raw")

    def test_translate_429_carries_retry_after(self):
        rest = AlpacaREST(alpaca_config=AlpacaConfig(api_key="k", api_secret="s"))
        translated = rest._translate(_api_error(429, headers={"Retry-After": "7"}))
        assert getattr(translated, "retry_after", None) == 7.0


class TestAlpacaTimeframes:
    @pytest.mark.parametrize(
        ("canonical", "amount", "unit"),
        [
            ("1m", 1, "Min"),
            ("5m", 5, "Min"),
            ("15m", 15, "Min"),
            ("30m", 30, "Min"),
            ("1h", 1, "Hour"),
            ("4h", 4, "Hour"),
            ("1d", 1, "Day"),
            ("1w", 1, "Week"),
            ("1mo", 1, "Month"),
        ],
    )
    def test_all_canonical_resolve(self, canonical, amount, unit):
        resolved = alpaca_resolution(canonical)
        assert resolved.amount == amount
        assert resolved.unit.value == unit
        assert str(resolved) == f"{amount}{unit}"

    def test_enum_input(self):
        assert alpaca_resolution(Timeframe.H1).amount == 1

    def test_no_missing_members(self):
        from datakodo.core.timeframe import ALPACA_MAP

        assert set(ALPACA_MAP) == set(Timeframe)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown timeframe"):
            alpaca_resolution("3h")


def _bar(ts, o=100.0, h=101.0, lo=99.0, c=100.5, v=1000.0, n=10, vw=100.2):
    """Build a real SDK Bar from wire-shape dict (offline, no network)."""
    return Bar("AAPL", {"t": ts, "o": o, "h": h, "l": lo, "c": c, "v": v, "n": n, "vw": vw})


class _FakeBarSet:
    def __init__(self, rows):
        self._rows = rows

    def __getitem__(self, symbol):
        return self._rows


class TestAlpacaGetBars:
    def _capture_call(self, monkeypatch, result):
        calls = []

        def _fake_call(self, method, *args, **kwargs):
            calls.append((method, args[0], kwargs))
            return result

        monkeypatch.setattr(AlpacaREST, "_call", _fake_call)
        return calls

    def test_stock_request_defaults(self, monkeypatch):
        from datetime import datetime

        start = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2024, 1, 3, tzinfo=UTC)
        calls = self._capture_call(monkeypatch, _FakeBarSet([]))
        rest = AlpacaREST(alpaca_config=AlpacaConfig(api_key="k", api_secret="s"))
        assert rest.get_bars("AAPL", "1d", start, end) == []
        method, request, kwargs = calls[0]
        assert method == "get_stock_bars"
        assert kwargs == {}
        assert isinstance(request, StockBarsRequest)
        assert request.symbol_or_symbols == "AAPL"
        assert str(request.timeframe) == "1Day"
        assert request.feed == DataFeed.IEX
        assert request.adjustment == Adjustment.RAW

    def test_explicit_feed_adjustment(self, monkeypatch):
        from datetime import datetime

        calls = self._capture_call(monkeypatch, _FakeBarSet([]))
        rest = AlpacaREST(alpaca_config=AlpacaConfig(api_key="k", api_secret="s"))
        rest.get_bars(
            "AAPL",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
            feed="sip",
            adjustment="split",
        )
        _, request, _ = calls[0]
        assert request.feed == DataFeed.SIP
        assert request.adjustment == Adjustment.SPLIT

    def test_crypto_routing(self, monkeypatch):
        from datetime import datetime

        calls = self._capture_call(monkeypatch, _FakeBarSet([]))
        rest = AlpacaREST(alpaca_config=AlpacaConfig(api_key="k", api_secret="s"))
        rest.get_bars(
            "BTC/USD", "1h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        method, request, kwargs = calls[0]
        assert method == "get_crypto_bars"
        assert kwargs.get("client") == "crypto"
        assert isinstance(request, CryptoBarsRequest)

    def test_unknown_symbol_returns_empty(self, monkeypatch):
        from datetime import datetime

        class _Missing(_FakeBarSet):
            def __getitem__(self, symbol):
                raise KeyError(symbol)

        self._capture_call(monkeypatch, _Missing([]))
        rest = AlpacaREST(alpaca_config=AlpacaConfig(api_key="k", api_secret="s"))
        assert (
            rest.get_bars(
                "NOPE", "1d", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
            )
            == []
        )

    def test_bad_timeframe_raises_value_error(self, monkeypatch):
        from datetime import datetime

        self._capture_call(monkeypatch, _FakeBarSet([]))
        rest = AlpacaREST(alpaca_config=AlpacaConfig(api_key="k", api_secret="s"))
        with pytest.raises(ValueError, match="Unknown timeframe"):
            rest.get_bars(
                "AAPL", "3h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
            )


class TestAlpacaFetchOHLCV:
    def _adapter_with_bars(self, monkeypatch, rows):
        adapter = AlpacaAdapter(api_key="k", api_secret="s")
        monkeypatch.setattr(AlpacaREST, "get_bars", lambda self, *a, **k: rows)
        return adapter

    def test_basic_mapping(self, monkeypatch):
        adapter = self._adapter_with_bars(
            monkeypatch,
            [_bar("2024-01-02T14:30:00Z"), _bar("2024-01-02T15:30:00Z", c=101.5)],
        )
        df = adapter.fetch_ohlcv(
            "AAPL", "1h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 3, tzinfo=UTC)
        )
        assert list(df.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "is_closed",
        ]
        assert len(df) == 2
        assert df["is_closed"].all()
        assert df.iloc[0]["open"] == 100.0
        assert df.iloc[-1]["close"] == 101.5
        assert str(df["timestamp"].dt.tz) == "UTC"

    def test_columns_all_and_list(self, monkeypatch):
        rows = [_bar("2024-01-02T14:30:00Z")]
        adapter = self._adapter_with_bars(monkeypatch, rows)
        start, end = datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 3, tzinfo=UTC)
        df_all = adapter.fetch_ohlcv("AAPL", "1h", start, end, columns="all")
        assert {"session", "vwap", "trades_count"} <= set(df_all.columns)
        assert df_all.iloc[0]["session"] == "regular"
        df_vwap = adapter.fetch_ohlcv("AAPL", "1h", start, end, columns=["vwap"])
        assert list(df_vwap.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "is_closed",
            "vwap",
        ]
        with pytest.raises(ValueError, match="not available"):
            adapter.fetch_ohlcv("AAPL", "1h", start, end, columns=["quote_volume"])

    def test_crypto_has_no_session(self, monkeypatch):
        adapter = self._adapter_with_bars(monkeypatch, [_bar("2024-01-02T14:30:00Z")])
        df = adapter.fetch_ohlcv(
            "BTC/USD",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 3, tzinfo=UTC),
            columns="all",
        )
        assert df.iloc[0]["session"] is None

    def test_include_live_filters_forming_bar(self, monkeypatch):
        forming = datetime.now(UTC).replace(second=0, microsecond=0).isoformat()
        rows = [_bar("2024-01-02T14:30:00Z"), _bar(forming)]
        adapter = self._adapter_with_bars(monkeypatch, rows)
        start, end = datetime(2024, 1, 1, tzinfo=UTC), datetime.now(UTC)
        assert len(adapter.fetch_ohlcv("AAPL", "1h", start, end)) == 1
        df_live = adapter.fetch_ohlcv("AAPL", "1h", start, end, include_live=True)
        assert len(df_live) == 2
        assert not df_live.iloc[-1]["is_closed"]

    def test_empty_raises_not_available(self, monkeypatch):
        adapter = self._adapter_with_bars(monkeypatch, [])
        with pytest.raises(DataNotAvailableError):
            adapter.fetch_ohlcv(
                "AAPL", "1h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 3, tzinfo=UTC)
            )

    def test_bad_timeframe_raises_invalid(self, monkeypatch):
        adapter = AlpacaAdapter(api_key="k", api_secret="s")

        def _boom(self, *a, **k):
            raise ValueError("Unknown timeframe: 3h")

        monkeypatch.setattr(AlpacaREST, "get_bars", _boom)
        with pytest.raises(InvalidTimeframeError):
            adapter.fetch_ohlcv(
                "AAPL", "3h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 3, tzinfo=UTC)
            )

    def test_default_range_last_30_days(self, monkeypatch):
        seen = {}

        def _fake_get_bars(self, symbol, timeframe, start, end, **kwargs):
            seen["start"], seen["end"] = start, end
            return [_bar("2024-01-02T14:30:00Z")]

        monkeypatch.setattr(AlpacaREST, "get_bars", _fake_get_bars)
        AlpacaAdapter(api_key="k", api_secret="s").fetch_ohlcv("AAPL", "1h")
        assert (seen["end"] - seen["start"]).days == 30


def _asset(symbol, asset_class, exchange="NASDAQ", name="Apple Inc."):
    return SimpleNamespace(symbol=symbol, asset_class=asset_class, exchange=exchange, name=name)


class TestAlpacaSearch:
    def test_map_instrument_classes(self):
        equity = map_instrument("AAPL", _asset("AAPL", AlpacaAssetClass.US_EQUITY))
        assert equity.asset_class == AssetClass.EQUITY
        assert equity.instrument_type == InstrumentType.SPOT
        assert equity.provider_symbol == "AAPL"
        assert equity.currency == "USD"

        crypto = map_instrument(
            "BTC/USD", _asset("BTC/USD", AlpacaAssetClass.CRYPTO, exchange="CRYPTO")
        )
        assert crypto.asset_class == AssetClass.CRYPTO
        assert crypto.instrument_type == InstrumentType.SPOT

        option = map_instrument("AAPL240119C00150000", _asset("X", AlpacaAssetClass.US_OPTION))
        assert option.instrument_type == InstrumentType.OPTION

    def test_map_instrument_enum_exchange(self):
        inst = map_instrument(
            "MSFT", _asset("MSFT", AlpacaAssetClass.US_EQUITY, exchange=AssetExchange.NYSE)
        )
        assert inst.exchange == "NYSE"

    def _adapter_with_assets(self, monkeypatch, assets):
        adapter = AlpacaAdapter(api_key="k", api_secret="s")
        monkeypatch.setattr(AlpacaREST, "list_assets", lambda self: assets)
        return adapter

    def test_query_matches_symbol_and_name(self, monkeypatch):
        adapter = self._adapter_with_assets(
            monkeypatch,
            [
                _asset("AAPL", AlpacaAssetClass.US_EQUITY),
                _asset("MSFT", AlpacaAssetClass.US_EQUITY, name="Microsoft"),
            ],
        )
        assert [i.symbol for i in adapter.search_instruments("aapl")] == ["AAPL"]
        assert [i.symbol for i in adapter.search_instruments("micro")] == ["MSFT"]

    def test_filters_combinable(self, monkeypatch):
        adapter = self._adapter_with_assets(
            monkeypatch,
            [
                _asset("AAPL", AlpacaAssetClass.US_EQUITY),
                _asset("BTC/USD", AlpacaAssetClass.CRYPTO, exchange="CRYPTO", name="Bitcoin"),
                _asset("O", AlpacaAssetClass.US_OPTION, name="Option"),
            ],
        )

        def _symbols(**kwargs):
            return [i.symbol for i in adapter.search_instruments("", **kwargs)]

        assert _symbols(asset_class=AssetClass.CRYPTO) == ["BTC/USD"]
        assert _symbols(asset_class="us_equity") == ["AAPL", "O"]
        assert _symbols(instrument_type=InstrumentType.OPTION) == ["O"]
        assert _symbols(quote="usd") == ["AAPL", "BTC/USD", "O"]
        assert _symbols(exchange="crypto") == ["BTC/USD"]
        assert _symbols(quote="EUR") == []

    def test_limit_caps_results(self, monkeypatch):
        adapter = self._adapter_with_assets(
            monkeypatch, [_asset(f"S{i}", AlpacaAssetClass.US_EQUITY, name="") for i in range(5)]
        )
        assert len(adapter.search_instruments("", limit=2)) == 2

    def test_list_assets_routing(self, monkeypatch):
        calls = []

        def _fake_call(self, method, *args, **kwargs):
            calls.append((method, kwargs))
            return ["x"]

        monkeypatch.setattr(AlpacaREST, "_call", _fake_call)
        rest = AlpacaREST(alpaca_config=AlpacaConfig(api_key="k", api_secret="s"))
        assert rest.list_assets() == ["x"]
        assert calls == [("get_all_assets", {"client": "assets"})]


class _RestrictedAlpaca(AlpacaAdapter):
    """Alpaca adapter pretending to offer only 1m and 1h natively."""

    native_timeframes = (Timeframe.M1, Timeframe.H1)


def test_fetch_ohlcv_resamples_non_native_timeframe(monkeypatch):
    """4h requested, only 1h native -> fetch 1h bars and resample to one 4h bar."""
    adapter = _RestrictedAlpaca(api_key="k", api_secret="s")
    rows = [
        _bar("2024-01-01T00:00:00Z", o=100.0, h=110.0, lo=90.0, c=105.0, v=1000.0),
        _bar("2024-01-01T01:00:00Z", o=105.0, h=120.0, lo=100.0, c=115.0, v=2000.0),
        _bar("2024-01-01T02:00:00Z", o=115.0, h=130.0, lo=110.0, c=125.0, v=3000.0),
        _bar("2024-01-01T03:00:00Z", o=125.0, h=140.0, lo=120.0, c=135.0, v=4000.0),
    ]
    monkeypatch.setattr(AlpacaREST, "get_bars", lambda self, *a, **k: rows)

    df = adapter.fetch_ohlcv(
        "AAPL", "4h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
    )
    assert len(df) == 1
    assert df.loc[0, "open"] == 100.0
    assert df.loc[0, "high"] == 140.0
    assert df.loc[0, "low"] == 90.0
    assert df.loc[0, "close"] == 135.0
    assert df.loc[0, "volume"] == 10000.0
    assert df.loc[0, "is_closed"]


def test_fetch_ohlcv_native_timeframe_is_not_resampled(monkeypatch):
    """A natively offered timeframe is fetched directly, no resampling."""
    adapter = _RestrictedAlpaca(api_key="k", api_secret="s")
    captured = {}

    def _fake_get_bars(self, symbol, timeframe, start, end, **kwargs):
        captured["timeframe"] = timeframe
        return [_bar("2024-01-01T00:00:00Z")]

    monkeypatch.setattr(AlpacaREST, "get_bars", _fake_get_bars)
    adapter.fetch_ohlcv(
        "AAPL", "1h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
    )
    assert captured["timeframe"] == "1h"
