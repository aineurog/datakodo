"""MT5 adapter tests."""

from datetime import UTC

import numpy as np
import pandas as pd
import pytest

from datakodo.adapters.mt5.adapter import MT5Adapter
from datakodo.adapters.mt5.mapper import VOLUME_BASELINES, map_ohlcv
from datakodo.core.exceptions import ProviderError

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


if __name__ == "__main__":
    _demo()
