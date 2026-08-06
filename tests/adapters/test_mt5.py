"""MT5 adapter tests."""

import logging
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from datakodo.adapters.mt5.adapter import MT5Adapter
from datakodo.adapters.mt5.mapper import VOLUME_BASELINES, map_ohlcv
from datakodo.adapters.mt5.terminal import MT5Terminal, _as_utc
from datakodo.core.config import Config
from datakodo.core.exceptions import ConnectionError, ProviderError

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
            "session",
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
            "session",
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
            "session",
        ]
        assert len(out) == 2
        assert out.iloc[0]["volume"] == 1000
        assert out.iloc[1]["volume"] == 1001
        assert out.iloc[0]["open"] == 1.10
        assert out.iloc[0]["close"] == 1.105

    def test_session_defaults_to_n_a(self):
        out = map_ohlcv(_make_rates(1))
        assert set(out["session"].unique()) == {"n/a"}

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


# --- terminal.MT5Terminal (real connection) --------------------------------


def _mt5_available() -> bool:
    """True when the real ``MetaTrader5`` module can be imported."""
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
    """A live ``MT5Terminal`` connected with the real config (.env).

    Skips the test when the terminal cannot be reached (e.g. not running,
    bad credentials, off-Windows CI) instead of failing it.
    """
    term = MT5Terminal(config=Config())
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

    def test_real_account_credentials_match_config(self, real_terminal):
        import MetaTrader5 as mt5

        info = mt5.account_info()
        assert info is not None
        cfg = Config()
        assert info.login == cfg.mt5_login
        assert info.server == cfg.mt5_server

    def test_copy_rates_range_returns_real_bars(self, real_terminal):
        from datetime import timedelta

        raw = real_terminal.copy_rates_range(
            "EURUSD", 1, datetime.now(UTC) - timedelta(hours=3), datetime.now(UTC)
        )
        assert raw is not None
        assert len(raw) > 0
        assert "time" in raw.dtype.names
        assert "open" in raw.dtype.names

    def test_copy_rates_range_unknown_symbol_returns_none(self, real_terminal):
        raw = real_terminal.copy_rates_range(
            "NONEXISTENT_SYMBOL",
            1,
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
        )
        assert raw is None

    def test_shutdown_disconnects(self, real_terminal):
        real_terminal.shutdown()
        assert real_terminal.connected is False
        with pytest.raises(ConnectionError):
            real_terminal.copy_rates_range(
                "EURUSD", 1, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
            )


class TestTerminalFeedback:
    """Feedback shown when login/history conditions need attention."""

    def test_initialize_warns_when_no_account_logged_in(self, caplog, monkeypatch):
        from datakodo.adapters.mt5 import terminal as terminal_mod

        # Terminal opens (initialize True) but no account is active.
        fake = type(
            "FakeMT5",
            (),
            {
                "initialize": lambda *a, **k: True,
                "last_error": lambda: (0, "ok"),
                "account_info": lambda: None,
                "shutdown": lambda: None,
            },
        )
        monkeypatch.setattr(terminal_mod, "_load_mt5", lambda: fake)
        term = terminal_mod.MT5Terminal()
        with caplog.at_level(logging.WARNING):
            term.initialize()
        assert term.connected is True
        assert any("no account is logged in" in r.message for r in caplog.records)

    def test_copy_rates_range_warns_when_no_history(self, caplog):
        from datakodo.adapters.mt5 import terminal as terminal_mod

        tick = type("Tick", (), {"time": int(datetime.now(UTC).timestamp()) + 10800})
        fake = type(
            "FakeMT5",
            (),
            {
                "symbol_info_tick": lambda s: tick,
                "copy_rates_range": lambda *a, **k: None,
            },
        )
        term = terminal_mod.MT5Terminal()
        term._connected = True
        term._mt5 = fake
        with caplog.at_level(logging.WARNING):
            out = term.copy_rates_range(
                "XAUUSD",
                16385,
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 2, tzinfo=UTC),
            )
        assert out is None
        assert any("No XAUUSD history returned" in r.message for r in caplog.records)


class TestMT5AdapterReal:
    def test_fetch_ohlcv_returns_canonical_frame(self):
        from datetime import timedelta

        from datakodo.adapters.mt5.adapter import MT5Adapter

        with MT5Adapter(terminal_path="") as adapter:
            df = adapter.fetch_ohlcv(
                "EURUSD",
                "1h",
                datetime.now(UTC) - timedelta(hours=24),
                datetime.now(UTC),
            )
        assert list(df.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "session",
        ]
        assert len(df) > 0
        assert df["session"].nunique() == 1

    def test_fetch_ohlcv_include_live_keeps_forming_bar(self):
        """include_live=True returns one more bar than the default closed-only."""
        from datetime import timedelta

        from datakodo.adapters.mt5.adapter import MT5Adapter

        start = datetime.now(UTC) - timedelta(hours=3)
        end = datetime.now(UTC)
        with MT5Adapter(terminal_path="") as adapter:
            closed = adapter.fetch_ohlcv("EURUSD", "1h", start, end, include_live=False)
            live = adapter.fetch_ohlcv("EURUSD", "1h", start, end, include_live=True)
        assert len(closed) > 0
        assert len(live) >= len(closed)
        if len(live) > len(closed):
            # the extra row is exactly one bar past the last closed one
            assert live["timestamp"].iloc[-1] > closed["timestamp"].iloc[-1]

    def test_fetch_ohlcv_invalid_timeframe_raises(self):
        from datakodo.core.exceptions import InvalidTimeframeError

        with MT5Adapter(terminal_path="") as adapter:
            with pytest.raises(InvalidTimeframeError):
                adapter.fetch_ohlcv(
                    "EURUSD",
                    "7h",
                    datetime(2026, 1, 1, tzinfo=UTC),
                    datetime(2026, 1, 2, tzinfo=UTC),
                )

    def test_fetch_ohlcv_not_connected_raises(self):
        from datetime import datetime

        from datakodo.core.exceptions import ConnectionError

        adapter = MT5Adapter()
        now = datetime.now(UTC)
        with pytest.raises(ConnectionError):
            adapter.fetch_ohlcv("EURUSD", "1h", now, now)

    def test_fetch_ohlcv_4h_logs_gaps_if_any(self, caplog):
        """Fetch real 4h data and verify gap detection logs if gaps exist."""
        from datakodo.adapters.mt5.adapter import MT5Adapter

        with MT5Adapter(terminal_path="") as adapter:
            with caplog.at_level(logging.WARNING):
                df = adapter.fetch_ohlcv(
                    "EURUSD",
                    "4h",
                    datetime.now(UTC) - timedelta(days=7),
                    datetime.now(UTC),
                )
        assert len(df) > 0
        # Just verify the call completes; if there are gaps, a warning is logged
        # (Weekends create natural gaps in forex — 4h will have ~2 missing per weekend)
        # We don't assert on specific gap count since it depends on the data

    def test_fetch_ohlcv_xauusd_weekend_finds_gaps(self, caplog):
        """XAUUSD over a weekend must surface market-closed gaps as warnings."""
        from datakodo.adapters.mt5.adapter import MT5Adapter
        from datakodo.ops.validation import detect_gaps

        # Thu 00:00 → Tue 00:00 UTC spans the closed weekend. Both sides of the
        # gap (last Friday bar → first Monday bar) must be inside the window,
        # otherwise there's no boundary row for detect_gaps to flag.
        start = datetime(2026, 7, 30, 0, 0, tzinfo=UTC)
        end = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)

        with MT5Adapter(terminal_path="") as adapter:
            with caplog.at_level(logging.WARNING):
                df = adapter.fetch_ohlcv("XAUUSD", "4h", start, end)

        assert len(df) > 0
        gaps = detect_gaps(df, "4h")
        assert not gaps.empty, "Expected weekend gaps in XAUUSD 4h data"
        assert (gaps["gap_missing"] > 0).all()

        gap_msg = [r for r in caplog.records if "Gap detected in XAUUSD" in r.getMessage()]
        assert gap_msg, "Expected a gap warning log for XAUUSD"


class TestGapDetection:
    """Tests for gap detection (design doc sec 18)."""

    def test_detect_gaps_finds_missing_candles(self):
        """Gaps are flagged with correct missing count."""
        from datakodo.ops.validation import detect_gaps

        # 1h data with a 3-hour gap (2 missing candles)
        timestamps = [
            1717171200,  # 00:00
            1717174800,  # 01:00
            1717185600,  # 04:00  <-- gap: missing 02:00, 03:00
            1717189200,  # 05:00
        ]
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(timestamps, unit="s", utc=True),
                "open": [1.0] * 4,
                "high": [1.1] * 4,
                "low": [0.9] * 4,
                "close": [1.05] * 4,
                "volume": [100] * 4,
                "session": ["n/a"] * 4,
            }
        )
        gaps = detect_gaps(df, "1h")
        assert len(gaps) == 1
        assert int(gaps["gap_missing"].iloc[0]) == 2
        assert gaps["timestamp"].iloc[0] == pd.Timestamp(1717174800, unit="s", tz="UTC")

    def test_detect_gaps_simple_full_flow(self):
        """Simple end-to-end: raw MT5 rates with a gap → map → detect."""
        from datakodo.ops.validation import detect_gaps

        # 4 hourly MT5 bars, but skip the 02:00 bar (1 missing candle)
        raw = np.array(
            [
                (1717171200, 1.10, 1.11, 1.09, 1.10, 100, 5, 0),  # 00:00
                (1717174800, 1.10, 1.11, 1.09, 1.10, 100, 5, 0),  # 01:00
                (1717182000, 1.10, 1.11, 1.09, 1.10, 100, 5, 0),  # 03:00 (02:00 missing)
                (1717185600, 1.10, 1.11, 1.09, 1.10, 100, 5, 0),  # 04:00
            ],
            dtype=_RAW_DTYPE,
        )
        df = map_ohlcv(raw)  # raw MT5 → canonical OHLCV
        gaps = detect_gaps(df, "1h")  # find the missing candle

        assert len(gaps) == 1, "expected exactly one gap"
        row = gaps.iloc[0]
        assert row["timestamp"] == pd.Timestamp(1717174800, unit="s", tz="UTC")  # bar before gap
        assert int(row["gap_missing"]) == 1, "one candle (02:00) is missing"

    def test_detect_gaps_no_gaps_returns_empty(self):
        """Contiguous data returns empty DataFrame."""
        from datakodo.ops.validation import detect_gaps

        timestamps = [1717171200, 1717174800, 1717178400, 1717182000]  # contiguous 1h
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(timestamps, unit="s", utc=True),
                "open": [1.0] * 4,
                "high": [1.1] * 4,
                "low": [0.9] * 4,
                "close": [1.05] * 4,
                "volume": [100] * 4,
                "session": ["n/a"] * 4,
            }
        )
        gaps = detect_gaps(df, "1h")
        assert gaps.empty

    def test_fetch_ohlcv_logs_warning_on_gap(self, caplog):
        """Adapter logs warning when gaps are detected in fetched data."""
        from datakodo.adapters.mt5.adapter import MT5Adapter

        # We mock the terminal to return data with a gap
        from datakodo.adapters.mt5.terminal import MT5Terminal

        class GappyTerminal(MT5Terminal):
            def copy_rates_range(self, symbol, timeframe, start, end):
                # Return 1h data with a 2-hour gap (1 missing candle)
                from datetime import datetime

                import numpy as np

                times = [
                    int(datetime(2024, 1, 1, 0, tzinfo=UTC).timestamp()),
                    int(datetime(2024, 1, 1, 1, tzinfo=UTC).timestamp()),
                    int(datetime(2024, 1, 1, 3, tzinfo=UTC).timestamp()),  # gap: missing 02:00
                    int(datetime(2024, 1, 1, 4, tzinfo=UTC).timestamp()),
                ]
                dtype = np.dtype(
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
                arr = np.array(
                    [(t, 1.0, 1.1, 0.9, 1.05, 100, 5, 0) for t in times],
                    dtype=dtype,
                )
                return arr

        # Create adapter with our gappy terminal
        adapter = MT5Adapter(terminal_path="")
        adapter._terminal = GappyTerminal(config=Config())
        adapter._terminal._connected = True
        adapter._terminal._mt5 = type(
            "M",
            (),
            {
                "last_error": lambda: (0, "ok"),
                "symbol_info_tick": lambda s: type("T", (), {"time": 1786003200})(),
            },
        )

        with caplog.at_level(logging.WARNING):
            _ = adapter.fetch_ohlcv(
                "EURUSD",
                "1h",
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2024, 1, 1, 5, tzinfo=UTC),
            )

        assert any("Gap detected" in record.message for record in caplog.records)
        assert "EURUSD" in caplog.text
        assert "1h" in caplog.text
        assert "1 gap" in caplog.text or "1 gap" in caplog.text


class TestAsUtc:
    def test_naive_assumed_utc(self):
        assert _as_utc(datetime(2024, 1, 1, 12, 0)).utcoffset() == timedelta(0)

    def test_aware_timezone_converted_to_utc(self):
        from zoneinfo import ZoneInfo

        dt = datetime(2024, 1, 1, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        assert _as_utc(dt).utcoffset() == timedelta(0)


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


def _demo() -> None:
    """Print raw MT5 rates, the canonical OHLCV output, and terminal wiring."""
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

    print("\n" + "=" * 60)
    print("MT5 terminal demo (real MetaTrader5 connection)")
    print("=" * 60)

    if not _mt5_available():
        print("MetaTrader5 package not installed — skipping real connection demo.")
        return

    term = MT5Terminal(config=Config())

    print("\n--- term.connected before initialize ---")
    print(term.connected)

    print("\n--- copy_rates_range before initialize (should raise ConnectionError) ---")
    try:
        term.copy_rates_range("EURUSD", 1, datetime(2024, 1, 1), datetime(2024, 1, 2))
    except ConnectionError as exc:
        print(f"ConnectionError: {exc}")

    print("\n--- initialize() then copy_rates_range() ---")
    print("launching MT5 terminal (a terminal window may open)...")
    term.initialize()
    print(f"connected: {term.connected}")
    raw_rates = term.copy_rates_range(
        "XAUUSD", 1, datetime.now(UTC) - timedelta(hours=236), datetime.now(UTC)
    )
    if raw_rates is not None:
        df = map_ohlcv(raw_rates)
        print(f"real bars fetched: {len(df)}")
        print(df)
        print("\n--- detect_gaps on real XAUUSD 1m data ---")
        from datakodo.ops.validation import detect_gaps

        gaps = detect_gaps(df, "1m")
        print(f"{len(gaps)} gap(s), {int(gaps['gap_missing'].sum())} missing candle(s)")
        if not gaps.empty:
            print(gaps[["timestamp", "gap_missing"]].head(10))
    else:
        print("no bars returned for that range")

    print("\n--- shutdown() ---")
    term.shutdown()
    print(f"connected: {term.connected}")


if __name__ == "__main__":
    _demo()
