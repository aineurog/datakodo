"""Tests for the Binance configuration and its wiring into the adapter.

Covers the provider-specific ``BinanceConfig`` model (``BINANCE_`` env prefix,
design doc sec 15) plus the cross-cutting ``Config`` model (``DATAKODO_`` env
prefix), and verifies those settings are actually synced into ``BinanceREST``,
``BinanceWS``, and ``BinanceAdapter``. All tests are offline / mocked; no live
API calls.

Run:
    python -m pytest tests/adapters/test_binance_config.py -v
"""

import pytest

from datakodo.adapters.binance.adapter import BinanceAdapter
from datakodo.adapters.binance.config import BinanceConfig
from datakodo.adapters.binance.rest import BinanceREST
from datakodo.adapters.binance.ws import BinanceWS
from datakodo.core.config import Config

BINANCE_CFG = BinanceConfig(
    api_key="key-123",
    api_secret="secret-456",
    tld="us",
    testnet=True,
    market_type="futures",
    timeout=7.5,
    rate_limit_rate=50.0,
    rate_limit_burst=500,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Make tests hermetic: drop real BINANCE_* / DATAKODO_* env vars."""
    for prefix in ("BINANCE_", "DATAKODO_"):
        for key in (
            "API_KEY",
            "API_SECRET",
            "TESTNET",
            "MARKET_TYPE",
            "TLD",
            "TIMEOUT",
            "RATE_LIMIT_RATE",
            "RATE_LIMIT_BURST",
            "OUTPUT_FORMAT",
            "MAX_RETRIES",
            "RETRY_BASE_DELAY",
            "FLAG_RESAMPLE",
            "LOG_LEVEL",
        ):
            monkeypatch.delenv(prefix + key, raising=False)


# --- 1. BinanceConfig model ---------------------------------------------------


def test_binance_config_defaults():
    cfg = BinanceConfig(_env_file=None)
    assert cfg.api_key == ""
    assert cfg.api_secret == ""
    assert cfg.testnet is False
    assert cfg.tld == "com"
    assert cfg.market_type == "spot"
    assert cfg.timeout == 10.0
    assert cfg.rate_limit_rate == 100.0
    assert cfg.rate_limit_burst == 1000


def test_binance_config_env_override(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "k")
    monkeypatch.setenv("BINANCE_API_SECRET", "s")
    monkeypatch.setenv("BINANCE_TLD", "us")
    monkeypatch.setenv("BINANCE_TESTNET", "true")
    monkeypatch.setenv("BINANCE_MARKET_TYPE", "futures")
    cfg = BinanceConfig(_env_file=None)
    assert cfg.api_key == "k"
    assert cfg.api_secret == "s"
    assert cfg.tld == "us"
    assert cfg.testnet is True
    assert cfg.market_type == "futures"


def test_binance_config_explicit_kwargs_win():
    cfg = BinanceConfig(market_type="futures", tld="us", _env_file=None)
    assert cfg.market_type == "futures"
    assert cfg.tld == "us"
    assert cfg.timeout == 10.0  # untouched default


# --- 2. Core Config model (cross-cutting, DATAKODO_ prefix) -------------------


def test_config_holds_cross_cutting_settings():
    cfg = Config(_env_file=None)
    assert cfg.output_format == "pandas"
    assert cfg.max_retries == 3
    assert cfg.retry_base_delay == 1.0
    assert cfg.flag_resample is True
    assert cfg.log_level == "INFO"


def test_config_has_no_binance_or_cache_fields():
    cfg = Config(_env_file=None)
    dump = cfg.model_dump()
    assert "binance_api_key" not in dump
    assert "cache_enabled" not in dump
    assert "cache_dir" not in dump


# --- 3. BinanceConfig -> BinanceREST ------------------------------------------


def test_rest_takes_binance_config(monkeypatch):
    captured = {}

    class _Client:
        def __init__(self, api_key, api_secret, requests_params, ping, tld, testnet):
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret
            captured["timeout"] = requests_params["timeout"]
            captured["tld"] = tld
            captured["testnet"] = testnet

    monkeypatch.setattr("datakodo.adapters.binance.rest.Client", _Client)
    BinanceREST(binance_config=BINANCE_CFG)
    assert captured["api_key"] == "key-123"
    assert captured["api_secret"] == "secret-456"
    assert captured["tld"] == "us"
    assert captured["testnet"] is True
    assert captured["timeout"] == 7.5


def test_rest_uses_binance_rate_limits():
    rest = BinanceREST(binance_config=BINANCE_CFG)
    assert rest._limiter._rate == 50.0
    assert rest._limiter._burst == 500


# --- 4. BinanceConfig -> BinanceWS --------------------------------------------


def test_ws_uses_binance_config():
    ws = BinanceWS(binance_config=BINANCE_CFG)
    assert ws._binance is BINANCE_CFG
    assert ws._binance.tld == "us"
    assert ws._binance.testnet is True


# --- 5. BinanceConfig -> BinanceAdapter ---------------------------------------


def test_adapter_holds_config():
    adapter = BinanceAdapter(binance_config=BINANCE_CFG)
    assert adapter._binance is BINANCE_CFG
    assert adapter._rest._binance is BINANCE_CFG
    assert adapter._ws._binance is BINANCE_CFG


def test_adapter_explicit_keys_override_config():
    adapter = BinanceAdapter(
        api_key="override-key", api_secret="override-secret", binance_config=BINANCE_CFG
    )
    assert adapter._binance.api_key == "override-key"
    assert adapter._binance.api_secret == "override-secret"
    assert adapter._binance.tld == "us"  # non-key fields still from config


def test_adapter_default_market_type_from_config(monkeypatch):
    from datetime import UTC, datetime

    adapter = BinanceAdapter(binance_config=BINANCE_CFG)
    captured = {}

    def _fake_klines(symbol, interval, start, end, market_type="spot"):
        captured["market_type"] = market_type
        return [
            [
                1704067200000,  # open_time
                "40000.0",
                "41000.0",
                "39000.0",
                "40500.0",
                "12.5",
                1704067260000,  # close_time
                "500000.0",
                100,
                "6.25",
                "250000.0",
                0,
            ]
        ]

    monkeypatch.setattr(adapter._rest, "klines", _fake_klines)
    df = adapter.fetch_ohlcv(
        "BTCUSDT",
        "1h",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
    )
    assert captured["market_type"] == "futures"
    assert len(df) == 1


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
