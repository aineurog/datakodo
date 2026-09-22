"""Alpaca adapter tests — step 1 skeleton + step 2 transport (offline only)."""

import asyncio
import json
import time
from datetime import UTC, datetime
from importlib.metadata import entry_points
from types import SimpleNamespace

import pytest
from alpaca.common.exceptions import APIError
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout

from datakodo.adapters.alpaca.adapter import AlpacaAdapter
from datakodo.adapters.alpaca.config import AlpacaConfig
from datakodo.adapters.alpaca.rest import AlpacaREST, alpaca_resolution
from datakodo.core.config import Config
from datakodo.core.enums import Timeframe
from datakodo.core.exceptions import (
    AuthenticationError,
    ConnectionError,
    DataValidationError,
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
    def test_fetch_ohlcv_raises_not_supported(self):
        adapter = AlpacaAdapter(api_key="k", api_secret="s")
        now = datetime.now(UTC)
        with pytest.raises(NotSupportedError):
            adapter.fetch_ohlcv("AAPL", "1h", now, now)

    def test_unsupported_surfaces_raise_not_supported(self):
        adapter = AlpacaAdapter(api_key="k", api_secret="s")
        with pytest.raises(NotSupportedError):
            adapter.fetch_ticks("AAPL")
        with pytest.raises(NotSupportedError):
            adapter.fetch_orderbook_snapshot("AAPL")
        with pytest.raises(NotSupportedError):
            adapter.fetch_fundamentals("AAPL")
        with pytest.raises(NotSupportedError):
            adapter.search_instruments("AAPL")

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
    monkeypatch.setattr(AlpacaREST, "_ensure_client", lambda self: fake)
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
