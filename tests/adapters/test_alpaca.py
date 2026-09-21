"""Alpaca adapter tests — step 1 skeleton (offline, no credentials/network)."""

import asyncio
from datetime import UTC, datetime
from importlib.metadata import entry_points

import pytest

from datakodo.adapters.alpaca.adapter import AlpacaAdapter
from datakodo.adapters.alpaca.config import AlpacaConfig
from datakodo.core.exceptions import NotSupportedError

_ENV_VARS = (
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "APCA_DATA_BASE_URL",
    "APCA_FEED",
    "APCA_TIMEOUT",
    "APCA_RATE_LIMIT_RATE",
    "APCA_RATE_LIMIT_BURST",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Isolate tests from the developer's real environment (and .env)."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


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
