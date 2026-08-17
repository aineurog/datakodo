"""MT5 adapter tests."""

import logging
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from datakodo.adapters.mt5.adapter import MT5Adapter
from datakodo.adapters.mt5.config import MT5Config
from datakodo.adapters.mt5.mapper import (
    VOLUME_BASELINES,
    map_fundamentals,
    map_instrument,
    map_ohlcv,
)
from datakodo.adapters.mt5.rest import MT5REST, _as_utc
from datakodo.adapters.mt5.terminal import MT5Terminal
from datakodo.core.enums import AssetClass, InstrumentType
from datakodo.core.exceptions import (
    ConnectionError,
    DataNotAvailableError,
    InvalidTimeframeError,
    ProviderError,
    RateLimitError,
)

# MT5 CopyRates returns a numpy structured array with these named columns.
_RAW_DTYPE = np.dtype(
    [
        ("time", "<i8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("tick_volume", "<i8"),
        ("spread", "<i4"),
        ("real_volume", "<i8"),
    ]
)


def _make_rates(n: int, start_sec: int = 1717171200, step_sec: int = 3600) -> np.ndarray:
    """Build *n* raw MT5 rate rows with distinct, increasing UTC times."""
    rows = np.array(
        [(start_sec + i * step_sec, 1.10, 1.11, 1.09, 1.105, 1000 + i, 5, 0) for i in range(n)],
        dtype=_RAW_DTYPE,
    )
    return rows


# --- MT5Config (provider-specific settings, MT5_ env prefix) -----------------


class TestMT5Config:
    def test_defaults(self):
        cfg = MT5Config(_env_file=None)
        assert cfg.terminal_path == r"C:\Program Files\MetaTrader 5"
        assert cfg.login is None
        assert cfg.password == ""
        assert cfg.server == ""
        assert cfg.timeout == 10.0
        assert cfg.rate_limit_rate == 5.0
        assert cfg.rate_limit_burst == 10
        assert cfg.market_type == "forex"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("MT5_TERMINAL_PATH", r"D:\MetaTrader")
        monkeypatch.setenv("MT5_LOGIN", "12345")
        monkeypatch.setenv("MT5_PASSWORD", "pw")
        monkeypatch.setenv("MT5_SERVER", "Broker-Demo")
        monkeypatch.setenv("MT5_TIMEOUT", "2.5")
        monkeypatch.setenv("MT5_RATE_LIMIT_RATE", "3.0")
        monkeypatch.setenv("MT5_RATE_LIMIT_BURST", "20")
        cfg = MT5Config(_env_file=None)
        assert cfg.terminal_path == r"D:\MetaTrader"
        assert cfg.login == 12345
        assert cfg.password == "pw"
        assert cfg.server == "Broker-Demo"
        assert cfg.timeout == 2.5
        assert cfg.rate_limit_rate == 3.0
        assert cfg.rate_limit_burst == 20

    def test_explicit_kwargs_win(self):
        cfg = MT5Config(server="Override-Server", market_type="cfd", _env_file=None)
        assert cfg.server == "Override-Server"
        assert cfg.market_type == "cfd"
        assert cfg.rate_limit_rate == 5.0  # untouched default

    def test_login_no_default_when_unset(self):
        cfg = MT5Config(_env_file=None)
        assert cfg.login is None


# --- terminal.MT5Terminal (mocked) -----------------------------------------


class FakeMT5Module:
    """A module-shaped stand-in for the ``MetaTrader5`` package.

    ``initialize`` returns True by default; ``copy_rates_range`` fabricates
    rates so tests never need a live terminal.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, datetime, datetime]] = []
        self.select_calls: list[tuple[str, bool]] = []
        self.initialize_ok = True
        self.error = (0, "ok")
        self.account = SimpleNamespace(login=12345, server="Broker-Demo")
        self.tick = SimpleNamespace(time=int(time.time()))
        self.info = _symbol_info()  # EURUSD-like forex by default
        self.shutdown_called = False
        self.step = 60  # seconds between fabricated bars

    def initialize(self, *args, **kwargs) -> bool:
        self.init_args = args
        self.init_kwargs = kwargs
        return self.initialize_ok

    def last_error(self):
        return self.error

    def account_info(self):
        return self.account

    def symbol_info(self, symbol):
        return self.info

    def symbol_info_tick(self, symbol):
        return self.tick

    def symbol_select(self, symbol, enable=True):
        self.select_calls.append((symbol, enable))
        return True

    def copy_rates_range(self, symbol, timeframe, start, end):
        self.calls.append((symbol, timeframe, start, end))
        if end <= start:
            return None
        times = range(int(start.timestamp()), int(end.timestamp()), self.step)
        rows = np.array(
            [(t, 1.10, 1.11, 1.09, 1.105, 100 + t % 7, 5, 0) for t in times],
            dtype=_RAW_DTYPE,
        )
        return rows

    def shutdown(self):
        self.shutdown_called = True


@pytest.fixture
def fake_mt5(monkeypatch):
    """Patch ``_load_mt5`` to return a fresh ``FakeMT5Module``."""
    mod = FakeMT5Module()
    monkeypatch.setattr("datakodo.adapters.mt5.terminal._load_mt5", lambda: mod)
    return mod


class TestMT5TerminalMocked:
    def test_initialize_connects_and_stores_module(self, fake_mt5):
        term = MT5Terminal()
        assert term.initialize() is True
        assert term.connected is True
        assert term._mt5 is fake_mt5

    def test_initialize_idempotent(self, fake_mt5):
        term = MT5Terminal()
        term.initialize()
        fake_mt5.init_args = ()
        fake_mt5.init_kwargs = {}
        term.initialize()
        assert not fake_mt5.init_args  # second call must not re-initialize

    def test_initialize_failure_raises_connection_error(self, fake_mt5):
        fake_mt5.initialize_ok = False
        fake_mt5.error = (10006, "invalid login")
        term = MT5Terminal()
        with pytest.raises(ConnectionError, match="MT5 initialize\\(\\) failed"):
            term.initialize()
        assert term.connected is False

    def test_initialize_passes_credentials(self, fake_mt5):
        cfg = MT5Config(login=12345, password="pw", server="Broker-Demo", _env_file=None)
        term = MT5Terminal(mt5_config=cfg)
        term.initialize()
        assert fake_mt5.init_kwargs.get("login") == 12345
        assert fake_mt5.init_kwargs.get("password") == "pw"
        assert fake_mt5.init_kwargs.get("server") == "Broker-Demo"

    def test_initialize_warns_no_account(self, fake_mt5, caplog):
        fake_mt5.account = None
        term = MT5Terminal()
        with caplog.at_level(logging.WARNING):
            term.initialize()
        assert term.connected is True
        assert any("no account is logged in" in r.message for r in caplog.records)

    def test_shutdown_disconnects(self, fake_mt5):
        term = MT5Terminal()
        term.initialize()
        term.shutdown()
        assert term.connected is False
        assert fake_mt5.shutdown_called is True
        assert term._mt5 is None


# --- rest.MT5REST (mocked) ---------------------------------------------------


def _connected_rest(fake_mt5, cfg: MT5Config | None = None) -> MT5REST:
    """A connected terminal + MT5REST pair over the fake module."""
    term = MT5Terminal(mt5_config=cfg)
    term.initialize()
    return MT5REST(term)


class TestMT5RESTMocked:
    def test_not_connected_blocks_data_calls(self):
        rest = MT5REST(MT5Terminal())
        with pytest.raises(ConnectionError, match="not connected"):
            rest.copy_rates_range(
                "EURUSD", 1, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
            )
        with pytest.raises(ConnectionError, match="not connected"):
            rest.symbol_info("EURUSD")
        with pytest.raises(ConnectionError, match="not connected"):
            rest.symbol_info_tick("EURUSD")
        with pytest.raises(ConnectionError, match="not connected"):
            rest.symbol_select("EURUSD")

    def test_copy_rates_range_shifts_to_server_time(self, fake_mt5):
        fake_mt5.tick = SimpleNamespace(time=int(time.time()) + 3 * 3600)  # GMT+3
        rest = _connected_rest(fake_mt5)
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = start + timedelta(minutes=100)
        raw = rest.copy_rates_range("EURUSD", 1, start, end)
        assert raw is not None
        symbol, timeframe, req_start, req_end = fake_mt5.calls[0]
        assert req_start == start + timedelta(hours=3)  # shifted into server time
        assert req_end == end + timedelta(hours=3)

    def test_copy_rates_range_no_history_returns_none(self, fake_mt5, caplog):
        fake_mt5.tick = SimpleNamespace(time=int(time.time()))
        rest = _connected_rest(fake_mt5)
        with caplog.at_level(logging.WARNING):
            out = rest.copy_rates_range(
                "EURUSD", 1, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)
            )
        assert out is None
        assert any("No EURUSD history returned" in r.message for r in caplog.records)

    def test_server_offset_seconds_snaps_to_hour(self, fake_mt5):
        fake_mt5.tick = SimpleNamespace(time=int(time.time()) + 3 * 3600 + 55)
        rest = _connected_rest(fake_mt5)
        assert rest.server_offset_seconds("EURUSD") == 3 * 3600

    def test_server_offset_zero_when_no_tick(self, fake_mt5):
        fake_mt5.tick = None
        rest = _connected_rest(fake_mt5)
        assert rest.server_offset_seconds("EURUSD") == 0

    def test_server_offset_not_connected_raises(self):
        rest = MT5REST(MT5Terminal())
        with pytest.raises(ConnectionError, match="not connected"):
            rest.server_offset_seconds("EURUSD")

    def test_rate_limit_exhaust_raises(self, fake_mt5):
        cfg = MT5Config(rate_limit_rate=1.0, rate_limit_burst=1, _env_file=None)
        rest = _connected_rest(fake_mt5, cfg)
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = start + timedelta(minutes=1)
        rest.copy_rates_range("EURUSD", 1, start, end)
        with pytest.raises(RateLimitError):
            rest.copy_rates_range("EURUSD", 1, start, end)

    def test_rate_limit_refills_after_wait(self, fake_mt5):
        cfg = MT5Config(rate_limit_rate=100.0, rate_limit_burst=1, _env_file=None)
        rest = _connected_rest(fake_mt5, cfg)
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = start + timedelta(minutes=1)
        rest.copy_rates_range("EURUSD", 1, start, end)
        time.sleep(0.05)
        assert rest.copy_rates_range("EURUSD", 1, start, end) is not None

    def test_calc_modes_resolved_from_module(self, fake_mt5):
        fake_mt5.SYMBOL_CALC_MODE_FUTURES = 2
        fake_mt5.SYMBOL_CALC_MODE_EXCH_FUTURES = 33
        fake_mt5.SYMBOL_CALC_MODE_FOREX = 0
        rest = _connected_rest(fake_mt5)
        assert rest.futures_calc_modes() == frozenset({2, 33})
        assert rest.forex_calc_modes() == frozenset({0})

    def test_calc_modes_empty_when_not_connected(self):
        rest = MT5REST(MT5Terminal())
        assert rest.futures_calc_modes() == frozenset()
        assert rest.forex_calc_modes() == frozenset()


# --- terminal.MT5Terminal (live, Windows-gated) -----------------------------


def _mt5_available() -> bool:
    try:
        import MetaTrader5  # noqa: F401

        return True
    except ImportError:
        return False


needs_mt5 = pytest.mark.skipif(
    not _mt5_available(),
    reason="MetaTrader5 package not installed (Windows-only)",
)


@pytest.fixture
def real_terminal():
    """A live MT5Terminal connected with the default config.

    Skips when the terminal cannot be reached (not running, bad credentials,
    off-Windows) instead of failing the test.
    """
    term = MT5Terminal()
    try:
        term.initialize()
    except Exception:
        pytest.skip("Live MT5 terminal not reachable")
    yield term
    term.shutdown()


@needs_mt5
class TestMT5TerminalReal:
    def test_initialize_connects_to_real_terminal(self, real_terminal):
        assert real_terminal.connected is True

    def test_copy_rates_range_returns_real_bars(self, real_terminal):
        # A window ending at now: MT5 keeps the recent history buffer for M1,
        # but a window ending strictly in the past often comes back empty.
        rest = MT5REST(real_terminal)
        raw = rest.copy_rates_range(
            "EURUSD",
            1,
            datetime.now(UTC) - timedelta(days=7),
            datetime.now(UTC),
        )
        assert raw is not None
        assert len(raw) > 0
        assert "time" in raw.dtype.names
        assert "open" in raw.dtype.names

    def test_shutdown_disconnects(self, real_terminal):
        real_terminal.shutdown()
        assert real_terminal.connected is False
        rest = MT5REST(real_terminal)
        with pytest.raises(ConnectionError):
            rest.copy_rates_range(
                "EURUSD", 1, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
            )


# --- rest._as_utc ------------------------------------------------------------


class TestAsUtc:
    def test_naive_assumed_utc(self):
        assert _as_utc(datetime(2024, 1, 1, 12, 0)).utcoffset() == timedelta(0)

    def test_aware_zoneinfo_converted_to_utc(self):
        from zoneinfo import ZoneInfo

        dt = datetime(2024, 1, 1, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        assert _as_utc(dt).utcoffset() == timedelta(0)


# --- mapper.map_ohlcv -------------------------------------------------------


class TestMapOHLCV:
    def test_none_input_returns_empty_schema(self):
        out = map_ohlcv(None)
        assert out.empty
        assert list(out.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

    def test_empty_array_returns_empty_schema(self):
        out = map_ohlcv(np.array([], dtype=_RAW_DTYPE))
        assert out.empty
        assert list(out.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

    def test_converts_time_to_utc_datetime(self):
        out = map_ohlcv(_make_rates(1, start_sec=1717171200))
        assert out.iloc[0]["timestamp"] == pd.Timestamp("2024-05-31 16:00:00", tz="UTC")

    def test_column_order_and_values(self):
        out = map_ohlcv(_make_rates(2))
        assert list(out.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
        assert len(out) == 2
        assert out.iloc[0]["volume"] == 1000
        assert out.iloc[1]["volume"] == 1001
        assert out.iloc[0]["open"] == 1.10
        assert out.iloc[0]["close"] == 1.105

    def test_no_session_column_by_default(self):
        """Option A (decision): mapper emits base columns only; the adapter
        adds ``session`` for forex under ``columns="all"`` (design doc sec 3/9)."""
        out = map_ohlcv(_make_rates(1))
        assert "session" not in out.columns

    def test_time_shifted_to_utc_by_offset(self):
        raw = _make_rates(1, start_sec=1717171200)
        out = map_ohlcv(raw, offset_seconds=3 * 3600)  # GMT+3 server
        assert out.iloc[0]["timestamp"] == pd.Timestamp("2024-05-31 13:00:00", tz="UTC")

    def test_default_volume_baseline_is_tick_volume(self):
        rates = _make_rates(1)
        rates["tick_volume"][0] = 42
        rates["real_volume"][0] = 7
        out = map_ohlcv(rates)
        assert out.iloc[0]["volume"] == 42

    def test_real_volume_baseline(self):
        rates = _make_rates(1)
        rates["tick_volume"][0] = 42
        rates["real_volume"][0] = 7
        out = map_ohlcv(rates, volume="real_volume")
        assert out.iloc[0]["volume"] == 7

    def test_invalid_volume_baseline_raises(self):
        with pytest.raises(ProviderError):
            map_ohlcv(_make_rates(1), volume="not_a_baseline")

    def test_volume_baselines_constant(self):
        assert VOLUME_BASELINES == ("tick_volume", "real_volume")

    def test_raw_fields_versus_mapped_columns(self):
        """MT5's raw named fields vs. the canonical mapped columns.

        Every raw field is consumed somewhere: time → timestamp, tick_volume →
        volume (default), real_volume selectable via ``volume=`` or as an
        opt-in extra; ``spread`` appears only as an opt-in extra.
        """
        raw = _make_rates(1)
        assert list(raw.dtype.names) == [
            "time",
            "open",
            "high",
            "low",
            "close",
            "tick_volume",
            "spread",
            "real_volume",
        ]
        assert list(map_ohlcv(raw).columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

    def test_extras_append_raw_columns(self):
        out = map_ohlcv(_make_rates(2), extras=("spread", "real_volume"))
        assert list(out.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "spread",
            "real_volume",
        ]
        assert out["spread"].tolist() == [5, 5]
        assert out["real_volume"].tolist() == [0, 0]

    def test_extras_works_with_real_volume_baseline(self):
        """Extras are snapshotted before the baseline rename, so requesting
        ``real_volume`` as both the baseline and an extra stays consistent."""
        rates = _make_rates(1)
        rates["real_volume"][0] = 7
        out = map_ohlcv(rates, volume="real_volume", extras=("real_volume",))
        assert out.iloc[0]["volume"] == 7
        assert out.iloc[0]["real_volume"] == 7

    def test_extras_empty_frame_schema(self):
        out = map_ohlcv(None, extras=("spread",))
        assert out.empty
        assert "spread" in out.columns

    def test_extras_invalid_raises(self):
        with pytest.raises(ProviderError, match="Invalid extra column"):
            map_ohlcv(_make_rates(1), extras=("bogus",))


# --- mapper.map_instrument (spot vs futures classification) ------------------


def _symbol_info(**overrides) -> SimpleNamespace:
    """A mock ``SymbolInfo`` tuple with realistic defaults (EURUSD-like forex)."""
    fields = dict(
        name="EURUSD",
        path="Forex\\EURUSD",
        description="Euro vs US Dollar",
        exchange="",
        currency_base="EUR",
        currency_profit="USD",
        digits=5,
        point=1e-05,
        trade_calc_mode=0,
        trade_contract_size=100000.0,
        trade_tick_size=1e-05,
        trade_tick_value=1.0,
        expiration_time=0,
        bid=None,
        ask=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestMapInstrument:
    def test_forex_symbol(self):
        inst = map_instrument("EURUSD", _symbol_info())
        assert inst.symbol == "EURUSD"
        assert inst.asset_class == AssetClass.FOREX
        assert inst.instrument_type == InstrumentType.SPOT
        assert inst.currency == "USD"
        assert inst.forex is not None
        assert inst.forex.pip_size == 0.0001  # 5-digit quote, point * 10
        assert inst.forex.lot_size == 100000

    def test_forex_4_digit_pip_size_equals_point(self):
        info = _symbol_info(digits=4, point=0.0001)
        inst = map_instrument("EURUSD", info)
        assert inst.forex.pip_size == 0.0001

    def test_forex_jpy_3_digit_pip_size(self):
        info = _symbol_info(
            name="USDJPY",
            path="Forex\\USDJPY",
            currency_base="USD",
            currency_profit="JPY",
            digits=3,
            point=0.001,
        )
        inst = map_instrument("USDJPY", info)
        assert inst.forex.pip_size == 0.01

    def test_metal_via_base_currency(self):
        info = _symbol_info(
            name="XAUUSD",
            path="Commodities\\XAUUSD",
            currency_base="XAU",
            currency_profit="USD",
        )
        inst = map_instrument("XAUUSD", info)
        assert inst.asset_class == AssetClass.METAL
        assert inst.instrument_type == InstrumentType.SPOT
        assert inst.metal is not None
        assert inst.metal.unit == "oz"

    def test_futures_via_calc_mode(self):
        info = _symbol_info(
            name="GCZ24",
            path="Futures\\GCZ24",
            description="Gold Futures Dec 24",
            trade_calc_mode=33,  # SYMBOL_CALC_MODE_EXCH_FUTURES
            trade_contract_size=100.0,
            trade_tick_size=0.1,
            trade_tick_value=10.0,
            expiration_time=1734393600,  # 2024-12-17 UTC
            currency_base="XAU",
            currency_profit="USD",
        )
        inst = map_instrument("GCZ24", info, futures_modes=frozenset({33}))
        assert inst.instrument_type == InstrumentType.FUTURE
        assert inst.asset_class == AssetClass.METAL  # underlying is gold (sec 4)
        assert inst.future is not None
        assert inst.future.expiry == "2024-12-17"
        assert inst.future.contract_size == 100.0
        assert inst.future.tick_size == 0.1
        assert inst.future.multiplier == 100.0  # tick_value / tick_size
        assert inst.future.underlying == "Gold Futures Dec 24"

    def test_futures_detected_by_path_when_mode_unknown(self):
        """Broker symbols may not map to a known calc mode; the Market Watch
        path ('Futures\\...') is the fallback signal."""
        info = _symbol_info(
            name="ESZ24",
            path="Futures\\ESZ24",
            trade_calc_mode=1,  # SYMBOL_CALC_MODE_FUTURES
        )
        inst = map_instrument("ESZ24", info, futures_modes=frozenset({1}))
        assert inst.instrument_type == InstrumentType.FUTURE

    def test_futures_mismatched_market_type_raises(self):
        info = _symbol_info(
            name="GCZ24",
            path="Futures\\GCZ24",
            trade_calc_mode=33,
            expiration_time=1734393600,
        )
        with pytest.raises(ProviderError, match="not spot"):
            map_instrument("GCZ24", info, futures_modes=frozenset({33}), market_type="spot")

    def test_spot_forex_accepts_market_type_spot(self):
        inst = map_instrument("EURUSD", _symbol_info(), market_type="spot")
        assert inst.instrument_type == InstrumentType.SPOT

    def test_unknown_market_type_raises(self):
        with pytest.raises(ProviderError, match="market_type"):
            map_instrument("EURUSD", _symbol_info(), market_type="bogus")

    def test_cfd_index_via_path(self):
        info = _symbol_info(
            name="US500",
            path="Indices\\US500",
            trade_calc_mode=3,  # SYMBOL_CALC_MODE_CFDINDEX
            currency_base="USD",
            currency_profit="USD",
        )
        inst = map_instrument("US500", info)
        assert inst.asset_class == AssetClass.INDEX
        assert inst.instrument_type == InstrumentType.CFD  # CFD is a type (sec 4)

    def test_equity_via_path(self):
        info = _symbol_info(
            name="AAPL",
            path="Equities\\AAPL",
            trade_calc_mode=32,  # SYMBOL_CALC_MODE_EXCH_STOCKS
            currency_base="USD",
            currency_profit="USD",
        )
        inst = map_instrument("AAPL", info)
        assert inst.asset_class == AssetClass.EQUITY
        assert inst.instrument_type == InstrumentType.SPOT

    def test_crypto_via_path(self):
        info = _symbol_info(
            name="BTCUSD",
            path="Crypto\\BTCUSD",
            trade_calc_mode=2,  # SYMBOL_CALC_MODE_CFD
            currency_base="BTC",
            currency_profit="USD",
        )
        inst = map_instrument("BTCUSD", info)
        assert inst.asset_class == AssetClass.CRYPTO
        assert inst.instrument_type == InstrumentType.SPOT

    def test_fallback_to_index_cfd(self):
        info = _symbol_info(
            name="CUSTOM",
            path="Misc\\CUSTOM",
            trade_calc_mode=2,  # SYMBOL_CALC_MODE_CFD
            description="",
        )
        inst = map_instrument("CUSTOM", info)
        assert inst.instrument_type == InstrumentType.CFD  # never a raw enum gap

    def test_none_info_raises(self):
        with pytest.raises(ProviderError, match="No MT5 symbol info"):
            map_instrument("EURUSD", None)

    def test_forex_modes_are_resolved_not_hardcoded(self):
        """Spot forex detection follows the passed calc-mode set (build-aware)."""
        info = _symbol_info(trade_calc_mode=5, path="Custom\\PAIR")  # FOREX_NO_LEVERAGE
        # Same numeric value is treated as forex only if it is in forex_modes.
        inst = map_instrument("PAIR", info, forex_modes=frozenset({5}))
        assert inst.asset_class == AssetClass.FOREX
        # A build where the value means something else is NOT forex.
        inst = map_instrument("PAIR", info, forex_modes=frozenset({0}))
        assert inst.instrument_type == InstrumentType.CFD


# --- mapper.map_fundamentals ------------------------------------------------


class TestMapFundamentals:
    def test_forex_fundamentals(self):
        info = _symbol_info(name="EURUSD", path="Forex\\EURUSD")
        tick = SimpleNamespace(time=1717171200)
        f = map_fundamentals("EURUSD", info, tick=tick, futures_modes=frozenset({33}))
        assert f.symbol == "EURUSD"
        assert f.name == "Euro vs US Dollar"
        assert f.asset_class == AssetClass.FOREX
        assert f.instrument_type == InstrumentType.SPOT
        assert f.currency == "USD"
        assert f.as_of == pd.Timestamp("2024-05-31 16:00:00", tz="UTC")

    def test_as_of_none_without_tick(self):
        info = _symbol_info(name="EURUSD", path="Forex\\EURUSD")
        f = map_fundamentals("EURUSD", info)
        assert f.as_of is None

    def test_as_of_shifts_server_tick_time_to_utc(self):
        """tick.time is server time; offset_seconds brings it back to true UTC."""
        info = _symbol_info(name="EURUSD", path="Forex\\EURUSD")
        tick = SimpleNamespace(time=1717171200)  # 2024-05-31 16:00 server time (GMT+3)
        f = map_fundamentals("EURUSD", info, tick=tick, offset_seconds=3 * 3600)
        assert f.as_of == pd.Timestamp("2024-05-31 13:00:00", tz="UTC")

    def test_none_info_raises(self):
        with pytest.raises(ProviderError, match="No MT5 symbol info"):
            map_fundamentals("EURUSD", None)


def _mark_last_bar_open(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Wrap ``add_is_closed`` but force the last bar to be still forming."""
    from datakodo.ops.validation import add_is_closed

    out = add_is_closed(df, timeframe)
    out.iloc[-1, out.columns.get_loc("is_closed")] = False
    return out


class TestMT5Adapter:
    def test_adapter_capabilities(self):
        adapter = MT5Adapter()
        assert adapter.supports_ohlcv is True
        assert adapter.supports_ticks is False
        assert adapter.supports_streaming_orderbook is False

    def test_fetch_ohlcv_not_connected_raises(self):
        from datetime import datetime

        from datakodo.core.exceptions import ConnectionError

        adapter = MT5Adapter()
        now = datetime.now(UTC)
        with pytest.raises(ConnectionError):
            adapter.fetch_ohlcv("EURUSD", "1h", now, now)

    def _connected_adapter(self, fake_mt5) -> MT5Adapter:
        adapter = MT5Adapter()
        adapter.connect()
        return adapter

    def _fetch(self, fake_mt5, *, columns, info=None, include_live=False, output_format=None):
        if info is not None:
            fake_mt5.info = info
        adapter = self._connected_adapter(fake_mt5)
        start = datetime(2026, 1, 1, tzinfo=UTC)
        return adapter.fetch_ohlcv(
            "EURUSD",
            "1h",
            start,
            start + timedelta(days=2),
            columns=columns,
            include_live=include_live,
            output_format=output_format,
        )

    def test_fetch_ohlcv_basic_columns(self, fake_mt5):
        df = self._fetch(fake_mt5, columns="basic")
        assert list(df.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "is_closed",
        ]
        assert len(df) == 2880  # fake module emits 1 bar per minute over 2 days

    def test_fetch_ohlcv_selects_symbol_before_rates(self, fake_mt5):
        """The symbol is added to MarketWatch first, so MT5 downloads its history."""
        self._fetch(fake_mt5, columns="basic")
        assert fake_mt5.select_calls and fake_mt5.select_calls[0] == ("EURUSD", True)
        assert len(fake_mt5.calls) > 0
        # selection must happen before the first history request
        first_select = fake_mt5.select_calls[0]
        first_request = fake_mt5.calls[0]
        assert first_request[0] == first_select[0]

    def test_fetch_ohlcv_all_adds_session_for_forex(self, fake_mt5):
        df = self._fetch(fake_mt5, columns="all")
        assert list(df.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "is_closed",
            "session",
            "spread",
            "real_volume",
        ]
        assert set(df["session"]) == {"regular"}

    def test_fetch_ohlcv_all_exposes_raw_extras(self, fake_mt5):
        df = self._fetch(fake_mt5, columns="all")
        assert df["spread"].iloc[0] == 5
        assert df["real_volume"].iloc[0] == 0  # fake rows carry real_volume=0

    def test_fetch_ohlcv_explicit_session_forex(self, fake_mt5):
        df = self._fetch(fake_mt5, columns=["session"])
        assert "session" in df.columns
        assert "is_closed" in df.columns

    def test_fetch_ohlcv_explicit_raw_extra(self, fake_mt5):
        df = self._fetch(fake_mt5, columns=["spread"])
        assert "spread" in df.columns
        assert "session" not in df.columns

    def test_fetch_ohlcv_all_metal_no_session(self, fake_mt5):
        info = _symbol_info(
            name="XAUUSD",
            path="Commodities\\XAUUSD",
            currency_base="XAU",
            currency_profit="USD",
        )
        df = self._fetch(fake_mt5, columns="all", info=info)
        assert "session" not in df.columns
        assert "spread" in df.columns and "real_volume" in df.columns

    def test_fetch_ohlcv_explicit_session_metal_raises(self, fake_mt5):
        info = _symbol_info(
            name="XAUUSD",
            path="Commodities\\XAUUSD",
            currency_base="XAU",
            currency_profit="USD",
        )
        with pytest.raises(ValueError, match="not available"):
            self._fetch(fake_mt5, columns=["session"], info=info)

    def test_fetch_ohlcv_canonical_but_unoffered_column_raises(self, fake_mt5):
        with pytest.raises(ValueError, match="not available"):
            self._fetch(fake_mt5, columns=["vwap"])  # in canonical set, not in MT5

    def test_fetch_ohlcv_unknown_column_raises(self, fake_mt5):
        with pytest.raises(ValueError, match="Unknown OHLCV column"):
            self._fetch(fake_mt5, columns=["bogus"])

    def test_adapter_wires_mt5_config_credentials(self, fake_mt5):
        cfg = MT5Config(login=12345, password="pw", server="Broker-Demo", _env_file=None)
        adapter = MT5Adapter(mt5_config=cfg)
        adapter.connect()
        assert fake_mt5.init_kwargs.get("login") == 12345
        assert fake_mt5.init_kwargs.get("password") == "pw"
        assert fake_mt5.init_kwargs.get("server") == "Broker-Demo"

    def test_fetch_ohlcv_invalid_timeframe_raises(self):
        adapter = MT5Adapter()
        now = datetime.now(UTC)
        with pytest.raises(InvalidTimeframeError):
            adapter.fetch_ohlcv("EURUSD", "bogus", now, now)

    def test_fetch_ohlcv_empty_history_raises(self, fake_mt5):
        adapter = self._connected_adapter(fake_mt5)
        start = datetime(2026, 1, 1, tzinfo=UTC)
        with pytest.raises(DataNotAvailableError):
            adapter.fetch_ohlcv("EURUSD", "1h", start, start)

    def test_fetch_ohlcv_paginates_large_range(self, fake_mt5):
        """Ranges wider than ``max_bars`` are chunked and stitched (sec 12).

        The fake emits one hourly bar per hour; a 2-day ``1h`` window is 48
        bars, so ``max_bars=24`` splits it into 2 terminal calls that get
        concatenated, deduplicated, and re-sorted into a single frame.
        """
        fake_mt5.step = 3600  # one bar per hour
        cfg = MT5Config(max_bars=24, _env_file=None)
        adapter = MT5Adapter(mt5_config=cfg)
        adapter.connect()
        start = datetime(2026, 1, 1, tzinfo=UTC)
        df = adapter.fetch_ohlcv("EURUSD", "1h", start, start + timedelta(days=2))
        assert len(fake_mt5.calls) == 2  # 48 hourly bars / 24-bar chunks
        assert len(df) == 48
        assert df["timestamp"].is_monotonic_increasing
        assert df["timestamp"].is_unique

    def test_fetch_ohlcv_paginate_stays_rate_limited(self, fake_mt5):
        """Chunked fetches still draw from the token bucket (sec 17)."""
        cfg = MT5Config(max_bars=24, rate_limit_rate=1.0, rate_limit_burst=1, _env_file=None)
        adapter = MT5Adapter(mt5_config=cfg)
        adapter.connect()
        start = datetime(2026, 1, 1, tzinfo=UTC)
        with pytest.raises(RateLimitError):
            adapter.fetch_ohlcv("EURUSD", "1h", start, start + timedelta(days=2))

    def test_fetch_ohlcv_default_drops_forming_bar(self, fake_mt5, monkeypatch):
        monkeypatch.setattr("datakodo.adapters.mt5.adapter.add_is_closed", _mark_last_bar_open)
        df = self._fetch(fake_mt5, columns="basic")
        assert len(df) == 2879  # 2880 bars minus the still-forming one
        assert df["is_closed"].all()

    def test_fetch_ohlcv_include_live_keeps_forming_bar(self, fake_mt5, monkeypatch):
        monkeypatch.setattr("datakodo.adapters.mt5.adapter.add_is_closed", _mark_last_bar_open)
        df = self._fetch(fake_mt5, columns="basic", include_live=True)
        assert len(df) == 2880
        assert not df["is_closed"].iloc[-1]

    def test_fetch_ohlcv_output_format_polars(self, fake_mt5):
        out = self._fetch(fake_mt5, columns="basic", output_format="polars")
        import polars as pl

        assert isinstance(out, pl.DataFrame)

    def test_fetch_ohlcv_output_format_arrow(self, fake_mt5):
        out = self._fetch(fake_mt5, columns="basic", output_format="arrow")
        import pyarrow as pa

        assert isinstance(out, pa.Table)


def _demo() -> None:
    """Print raw MT5 rates and the canonical OHLCV output for inspection."""
    print("=" * 60)
    print("MT5 mapper demo (fabricated numpy structured array)")
    print("=" * 60)

    raw = _make_rates(3)
    raw["tick_volume"][0] = 42
    raw["real_volume"][0] = 7

    print("\n--- raw MT5 CopyRates array (as MT5 returns it) ---")
    print(raw)
    print(f"\ndtype fields: {raw.dtype.names}")

    print("\n--- map_ohlcv(raw)  [default volume=tick_volume] ---")
    out = map_ohlcv(raw)
    print(out)
    print(f"\ndtypes:\n{out.dtypes}")

    print("\n--- map_ohlcv(raw, volume='real_volume') ---")
    print(map_ohlcv(raw, volume="real_volume"))

    print("\n--- map_ohlcv(None)  [empty canonical schema] ---")
    print(map_ohlcv(None))

    print("\n--- ALL columns MT5 returns, at each layer ---")

    print("\n1) Raw copy_rates_range fields (everything MT5 gives):")
    print(f"   {list(raw.dtype.names)}")

    print("\n2) Mapped canonical frame (map_ohlcv, default volume=tick_volume):")
    print(f"   {list(map_ohlcv(raw).columns)}")

    print("\n3) Mapped frame with the other volume baseline:")
    print(f"   {list(map_ohlcv(raw, volume='real_volume').columns)}")

    print("\n4) fetch_ohlcv columns by selection:")
    from datakodo.adapters.mt5.adapter import _requests_session
    from datakodo.adapters.mt5.mapper import MT5_OHLCV_EXTRAS
    from datakodo.core.schemas import resolve_ohlcv_columns
    from datakodo.ops.validation import add_is_closed

    for columns in ("basic", "all", ["session"], ["spread", "real_volume"]):
        available = MT5_OHLCV_EXTRAS + (("session",) if _requests_session(columns) else ())
        resolved = resolve_ohlcv_columns(columns, available)
        mapped_extras = tuple(e for e in MT5_OHLCV_EXTRAS if e in resolved)
        df = map_ohlcv(_make_rates(2), extras=mapped_extras)
        df = add_is_closed(df, "1h")
        if "session" in resolved:
            df = df.assign(session="regular")
        print(f"   columns={columns!r:<14} -> {df[resolved].columns.tolist()}")

    print("\nNote: tick_volume folds into 'volume' (default baseline);")
    print("'spread' and 'real_volume' are opt-in extras under columns='all' or a list.")


if __name__ == "__main__":
    _demo()
