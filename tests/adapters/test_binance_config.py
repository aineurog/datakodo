"""Tests for the Binance configuration and its wiring into the adapter.

Covers the ``binance_*`` settings on the core ``Config`` model (design doc
sec 13/14 — a single user-editable ``.env``) and verifies that those
settings are actually synced into ``BinanceREST``, ``BinanceWS``, and
``BinanceAdapter``. All tests are offline / mocked; no live API calls.

Run:
    python -m pytest tests/adapters/test_binance_config.py -v
"""

from pathlib import Path

import pytest

from datakodo.adapters.binance.adapter import BinanceAdapter
from datakodo.adapters.binance.rest import BinanceREST
from datakodo.adapters.binance.ws import BinanceWS
from datakodo.core.config import Config

CFG = Config(
    binance_api_key="key-123",
    binance_api_secret="secret-456",
    binance_tld="us",
    binance_testnet=True,
    binance_market_type="futures",
    binance_timeout=7.5,
    binance_rate_limit_rate=50.0,
    binance_rate_limit_burst=500,
)

TEST_ENV = {
    "DATAKODO_BINANCE_API_KEY": "abc",
    "DATAKODO_BINANCE_API_SECRET": "def",
    "DATAKODO_BINANCE_TESTNET": "true",
    "DATAKODO_BINANCE_MARKET_TYPE": "futures",
    "DATAKODO_BINANCE_TLD": "jp",
    "DATAKODO_CACHE_DIR": "datakodo_test_cache",
    "DATAKODO_CACHE_ENABLED": "false",
}


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


# --- 1. Config model ------------------------------------------------------------


def test_config_binance_defaults():
    cfg = Config(_env_file=None)
    assert cfg.binance_api_key == ""
    assert cfg.binance_api_secret == ""
    assert cfg.binance_testnet is False
    assert cfg.binance_tld == "com"
    assert cfg.binance_market_type == "spot"
    assert cfg.binance_timeout == 10.0
    assert cfg.binance_rate_limit_rate == 100.0
    assert cfg.binance_rate_limit_burst == 1000


def test_config_uses_correct_env_prefix(monkeypatch):
    monkeypatch.setenv("DATAKODO_BINANCE_API_KEY", "k")
    monkeypatch.setenv("DATAKODO_BINANCE_TLD", "us")
    monkeypatch.setenv("DATAKODO_BINANCE_TESTNET", "true")
    cfg = Config(_env_file=None)
    assert cfg.binance_api_key == "k"
    assert cfg.binance_tld == "us"
    assert cfg.binance_testnet is True


def test_config_unrelated_prefix_ignored(monkeypatch):
    monkeypatch.setenv("SOME_OTHER_VAR", "should-ignore")
    cfg = Config(_env_file=None)
    assert "SOME_OTHER_VAR" not in cfg.model_dump()


def test_config_explicit_kwargs_win_over_defaults():
    cfg = Config(binance_market_type="futures", _env_file=None)
    assert cfg.binance_market_type == "futures"
    assert cfg.binance_tld == "com"  # untouched default


def test_config_env_overridable_per_field(monkeypatch):
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    cfg = Config(_env_file=None)
    assert cfg.binance_api_key == "abc"
    assert cfg.binance_api_secret == "def"
    assert cfg.binance_testnet is True
    assert cfg.binance_market_type == "futures"
    assert cfg.binance_tld == "jp"
    assert str(cfg.cache_dir) == "datakodo_test_cache"
    assert cfg.cache_enabled is False


# --- 2. Config -> BinanceREST -----------------------------------------------


def test_rest_takes_config(monkeypatch):
    captured = {}

    class _Client:
        def __init__(self, api_key, api_secret, requests_params, ping, tld, testnet):
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret
            captured["timeout"] = requests_params["timeout"]
            captured["tld"] = tld
            captured["testnet"] = testnet

    monkeypatch.setattr("datakodo.adapters.binance.rest.Client", _Client)
    BinanceREST(config=CFG)
    assert captured["api_key"] == "key-123"
    assert captured["api_secret"] == "secret-456"
    assert captured["tld"] == "us"
    assert captured["testnet"] is True
    assert captured["timeout"] == 7.5


def test_rest_explicit_keys_override_config(monkeypatch):
    captured = {}

    class _Client:
        def __init__(self, api_key, api_secret, **kwargs):
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret

    monkeypatch.setattr("datakodo.adapters.binance.rest.Client", _Client)
    BinanceREST("override-key", "override-secret", config=CFG)
    assert captured["api_key"] == "override-key"
    assert captured["api_secret"] == "override-secret"


def test_rest_uses_config_rate_limits():
    rest = BinanceREST(config=CFG)
    assert rest._limiter._rate == 50.0
    assert rest._limiter._burst == 500


# --- 3. Config -> BinanceWS ------------------------------------------------


def test_ws_uses_config_values():
    ws = BinanceWS(config=CFG)
    assert ws._config is CFG
    assert ws._config.binance_tld == "us"
    assert ws._config.binance_testnet is True


def test_ws_explicit_keys_override_config():
    ws = BinanceWS("override-key", "override-secret", config=CFG)
    cfg = ws._config
    assert cfg.binance_api_key != CFG.binance_api_key
    assert cfg.binance_api_secret != CFG.binance_api_secret
    assert cfg.binance_tld == "us"  # non-key fields still from config


# --- 4. Config -> BinanceAdapter ---------------------------------------------


def test_adapter_holds_config():
    adapter = BinanceAdapter(config=CFG)
    assert adapter._config == CFG
    assert adapter._rest._config == CFG
    assert adapter._ws._config == CFG


def test_adapter_storage_from_config(tmp_path):
    cfg = Config(cache_enabled=True, cache_dir=Path(tmp_path), binance_market_type="futures")
    adapter = BinanceAdapter(config=cfg)
    expected = str(tmp_path)
    assert adapter._storage._base_dir == Path(expected)


def test_adapter_storage_disabled_when_cache_off(tmp_path):
    cfg = Config(cache_enabled=False, cache_dir=Path(tmp_path))
    adapter = BinanceAdapter(config=cfg)
    assert str(adapter._storage._base_dir) == "."


def test_adapter_explicit_storage_wins(tmp_path):
    from datakodo.storage.parquet import ParquetBackend

    custom = ParquetBackend(base_dir=str(tmp_path / "custom"))
    adapter = BinanceAdapter(config=CFG, storage=custom)
    assert adapter._storage is custom


def test_adapter_default_market_type_from_config(monkeypatch):
    from datetime import UTC, datetime

    adapter = BinanceAdapter(config=CFG)
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
                1704067260000,
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
        persist=False,
    )
    assert captured["market_type"] == "futures"
    assert len(df) == 1


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
