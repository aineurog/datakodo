"""Massive adapter tests — all in one file.

Two groups:

  * unit tests (no network): config, timeframe map, rest helpers, the
    ``MassiveREST.aggs`` call via a fake SDK client, mapper, adapter wiring.
  * one live test: fetches real OHLCV with the key from ``.env`` and saves
    the result to a CSV so the data is easy to inspect. Skipped when no key
    is configured.

Run:
    python -m pytest tests/adapters/test_massive.py -v
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from datakodo.adapters.massive.adapter import MassiveAdapter
from datakodo.adapters.massive.mapper import map_ohlcv, map_ticks, map_trades
from datakodo.adapters.massive.rest import MassiveREST, _to_millis, translate_status
from datakodo.adapters.massive.ws import MassiveWS
from datakodo.core.config import Config
from datakodo.core.enums import Timeframe
from datakodo.core.exceptions import (
    AuthenticationError,
    DataLibError,
    DataNotAvailableError,
    PaidTierRequiredError,
    RateLimitError,
    SymbolNotFoundError,
)
from datakodo.core.timeframe import MASSIVE_TIMESPANS

# --- fixtures ---------------------------------------------------------------

RAW_AGG = {
    "o": 40000.0,
    "h": 41000.0,
    "l": 39000.0,
    "c": 40500.0,
    "v": 12.5,
    "vw": 40400.0,
    "t": 1704067200000,
    "n": 100,
}


def _agg_from_raw(raw: dict) -> SimpleNamespace:
    """Turn a raw agg dict into a fake SDK ``Agg`` object."""
    return SimpleNamespace(
        open=raw["o"],
        high=raw["h"],
        low=raw["l"],
        close=raw["c"],
        volume=raw["v"],
        vwap=raw["vw"],
        timestamp=raw["t"],
        transactions=raw["n"],
    )


def _trade_from_raw(raw: dict) -> SimpleNamespace:
    """Turn a raw trade dict into a fake SDK ``Trade`` object."""
    return SimpleNamespace(
        conditions=raw.get("c"),
        participant_timestamp=raw.get("y"),
        price=raw.get("p"),
        sip_timestamp=raw.get("t"),
        size=raw.get("s"),
        exchange=raw.get("x"),
        id=raw.get("i"),
        tape=raw.get("z"),
    )


class _FakeClient:
    """Fake massive SDK client: returns canned ``Agg``/``Trade`` objects."""

    def __init__(self, aggs: list[dict] | None = None, trades: list[dict] | None = None) -> None:
        self._aggs = [dict(a) for a in (aggs or [])]
        self._trades = [dict(t) for t in (trades or [])]
        self.calls: list[dict] = []

    def list_aggs(self, **kwargs):
        self.calls.append(kwargs)
        return iter([_agg_from_raw(a) for a in self._aggs])

    def list_trades(self, **kwargs):
        self.calls.append(kwargs)
        return iter([_trade_from_raw(t) for t in self._trades])


@pytest.fixture(autouse=True)
def _clear_datakodo_env(monkeypatch):
    """Make unit tests hermetic: drop real DATAKODO_* env vars."""
    for key in list(Config.model_fields):
        monkeypatch.delenv("DATAKODO_" + key.upper(), raising=False)


def _rest_with_fake_client(
    aggs: list[dict] | None = None, trades: list[dict] | None = None
) -> tuple[MassiveREST, _FakeClient]:
    rest = MassiveREST(api_key="k", rate_limit=(100.0, 1000))
    fake = _FakeClient(aggs, trades)
    rest._client = fake  # bypass the lazy builder
    return rest, fake# --- 1. Config --------------------------------------------------------------

def test_config_defaults():
    cfg = Config(_env_file=None)
    assert cfg.massive_api_key == ""
    assert cfg.massive_timeout == 10.0
    assert cfg.massive_rate_limit_rate == 5.0
    assert cfg.massive_rate_limit_burst == 50


def test_config_env_override(monkeypatch):
    monkeypatch.setenv("DATAKODO_MASSIVE_API_KEY", "secret-key")
    cfg = Config(_env_file=None)
    assert cfg.massive_api_key == "secret-key"


# --- 2. Timeframe mapping ---------------------------------------------------

def test_massive_timespans_covers_all_canonical_timeframes():
    assert set(MASSIVE_TIMESPANS) == set(Timeframe)


def test_massive_timespan_values():
    assert MASSIVE_TIMESPANS[Timeframe.M1] == (1, "minute")
    assert MASSIVE_TIMESPANS[Timeframe.M5] == (5, "minute")
    assert MASSIVE_TIMESPANS[Timeframe.H1] == (1, "hour")
    assert MASSIVE_TIMESPANS[Timeframe.H4] == (4, "hour")
    assert MASSIVE_TIMESPANS[Timeframe.D1] == (1, "day")
    assert MASSIVE_TIMESPANS[Timeframe.W1] == (1, "week")
    assert MASSIVE_TIMESPANS[Timeframe.MN1] == (1, "month")


# --- 3. REST helpers --------------------------------------------------------

def test_to_millis_aware():
    dt = datetime(2024, 1, 1, tzinfo=UTC)
    assert _to_millis(dt) == int(dt.timestamp() * 1000)


def test_to_millis_naive_assumed_utc():
    assert _to_millis(datetime(2024, 1, 1)) == _to_millis(datetime(2024, 1, 1, tzinfo=UTC))


def test_translate_not_authorized_plan():
    body = '{"status":"NOT_AUTHORIZED","message":"Your plan doesn\'t include this data timeframe."}'
    err = translate_status(body)
    assert isinstance(err, PaidTierRequiredError)
    assert "upgrade" in str(err).lower()
    assert "pricing" in str(err).lower()


def test_translate_not_authorized_bad_key():
    body = '{"status":"NOT_AUTHORIZED","message":"Invalid API key."}'
    assert isinstance(translate_status(body), AuthenticationError)


def test_translate_forbidden():
    body = '{"status":"FORBIDDEN","message":"Not entitled."}'
    assert isinstance(translate_status(body), DataNotAvailableError)


def test_translate_not_found():
    body = '{"status":"NOT_FOUND","message":"symbol not found."}'
    assert isinstance(translate_status(body), SymbolNotFoundError)


def test_translate_too_many_requests():
    body = '{"status":"TOO_MANY_REQUESTS","message":"Slow down."}'
    assert isinstance(translate_status(body), RateLimitError)


def test_translate_unknown_body():
    assert isinstance(translate_status("just text"), DataLibError)


# --- 4. MassiveREST.aggs ----------------------------------------------------

def test_aggs_calls_sdk_with_millis_and_params():
    rest, fake = _rest_with_fake_client([RAW_AGG])
    out = rest.aggs(
        "AAPL", 1, "hour",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
    )
    assert out[0].open == 40000.0
    assert out[0].timestamp == 1704067200000
    call = fake.calls[0]
    assert call["ticker"] == "AAPL"
    assert call["multiplier"] == 1
    assert call["timespan"] == "hour"
    assert call["from_"] == 1704067200000
    assert call["to"] == 1704153600000
    assert call["limit"] == 50000
    assert call["adjusted"] is True
    assert call["sort"] == "asc"


def test_aggs_returns_sdk_objects_not_dicts():
    rest, _ = _rest_with_fake_client([RAW_AGG])
    out = rest.aggs(
        "AAPL", 1, "day",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
    )
    assert not isinstance(out[0], dict)
    assert out[0].high == 41000.0
    assert out[0].volume == 12.5
    assert out[0].vwap == 40400.0


def test_aggs_empty_results():
    rest, _ = _rest_with_fake_client([])
    out = rest.aggs(
        "AAPL", 1, "hour",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 1, 2, tzinfo=UTC),
    )
    assert out == []


def test_aggs_no_key_raises():
    rest = MassiveREST(config=Config(_env_file=None))
    with pytest.raises(AuthenticationError):
        rest.aggs(
            "AAPL", 1, "day",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )


# --- 4b. MassiveREST.trades -------------------------------------------------

def test_trades_calls_sdk_with_nanos_and_params():
    rest, fake = _rest_with_fake_client()
    out = rest.trades(
        "AAPL",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
    )
    assert out == []
    call = fake.calls[0]
    assert call["ticker"] == "AAPL"
    assert call["timestamp_gte"] == 1704067200000 * 1_000_000
    assert call["timestamp_lte"] == 1704153600000 * 1_000_000
    assert call["limit"] == 50000


def test_trades_returns_sdk_objects_not_dicts():
    raw = {"t": 1704067200000000000, "p": 40500.0, "s": 2.0, "c": [2],
           "x": 1, "i": "123", "z": 3, "y": 1704067200000000000}
    rest, _ = _rest_with_fake_client(trades=[raw])
    out = rest.trades("AAPL")
    assert not isinstance(out[0], dict)
    assert out[0].sip_timestamp == 1704067200000000000
    assert out[0].price == 40500.0
    assert out[0].conditions == [2]


def test_trades_empty_results():
    rest, _ = _rest_with_fake_client(trades=[])
    out = rest.trades("AAPL")
    assert out == []


def test_trades_no_key_raises():
    rest = MassiveREST(config=Config(_env_file=None))
    with pytest.raises(AuthenticationError):
        rest.trades("AAPL")


# --- 5. Mapper --------------------------------------------------------------

def test_map_ohlcv_columns():
    df = map_ohlcv([RAW_AGG])
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume", "session"]
    assert df["timestamp"].iloc[0].tz is not None
    assert df["timestamp"].iloc[0] == pd.Timestamp(1704067200000, unit="ms", tz="UTC")
    assert df["open"].iloc[0] == 40000.0
    assert df["session"].iloc[0] == "regular"


def test_map_ohlcv_from_sdk_agg_object():
    df = map_ohlcv([_agg_from_raw(RAW_AGG)])
    assert df["timestamp"].iloc[0] == pd.Timestamp(1704067200000, unit="ms", tz="UTC")
    assert df["open"].iloc[0] == 40000.0
    assert df["volume"].iloc[0] == 12.5


def test_map_ohlcv_from_futures_sdk_object_window_start_ns():
    agg = SimpleNamespace(
        open=3900.0, high=3950.0, low=3880.0, close=3930.0, volume=100,
        window_start=1704067200000000000, dollar_volume=0, transactions=0,
    )
    df = map_ohlcv([agg])
    assert df["timestamp"].iloc[0] == pd.Timestamp(1704067200000, unit="ms", tz="UTC")
    assert df["close"].iloc[0] == 3930.0


def test_map_ohlcv_empty():
    df = map_ohlcv([])
    assert df.empty
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume", "session"]


def test_map_trades_ms_timestamp():
    trade = map_trades({"t": 1704067200000, "p": 100.0, "s": 5.0})
    assert trade.timestamp == pd.Timestamp(1704067200000, unit="ms", tz="UTC")
    assert trade.price == 100.0
    assert trade.size == 5.0


def test_map_trades_ns_timestamp():
    trade = map_trades({"t": 1704067200000000000, "p": 100.0, "s": 5.0})
    assert trade.timestamp == pd.Timestamp(1704067200000, unit="ms", tz="UTC")


def test_map_trades_from_sdk_trade_object():
    raw = {"t": 1704067200000000000, "p": 100.0, "s": 5.0, "c": [2]}
    trade = map_trades(_trade_from_raw(raw))
    assert trade.timestamp == pd.Timestamp(1704067200000, unit="ms", tz="UTC")
    assert trade.side == "buy"


def test_map_trades_crypto_buy_side():
    trade = map_trades({"t": 1704067200000, "p": 100.0, "s": 5.0, "c": [2]})
    assert trade.side == "buy"


def test_map_trades_crypto_sell_side():
    trade = map_trades({"t": 1704067200000, "p": 100.0, "s": 5.0, "c": 1})
    assert trade.side == "sell"


def test_map_trades_stock_side_unknown():
    trade = map_trades({"t": 1704067200000, "p": 100.0, "s": 5.0, "c": [18, 13]})
    assert trade.side is None


def test_map_ticks_list():
    ticks = map_ticks([
        {"t": 1704067200000, "p": 100.0, "s": 5.0, "c": [2]},
        {"t": 1704067201000, "p": 100.5, "s": 1.0, "c": [1]},
    ])
    assert len(ticks) == 2
    assert ticks[0].side == "buy"
    assert ticks[1].side == "sell"


# --- 6. Adapter -------------------------------------------------------------

def test_adapter_capabilities_and_native_timeframes():
    adapter = MassiveAdapter(config=Config(_env_file=None))
    assert adapter.supports_ohlcv is True
    assert adapter.supports_ticks is True
    assert adapter.supports_streaming_orderbook is False
    assert adapter.supports_fundamentals is True
    assert adapter.native_timeframes == tuple(Timeframe)


def test_adapter_config_plumbing():
    adapter = MassiveAdapter(config=Config(_env_file=None, massive_api_key="abc"))
    assert adapter._config.massive_api_key == "abc"
    assert adapter._rest._config.massive_api_key == "abc"


def test_adapter_ws_construction():
    assert isinstance(MassiveAdapter(config=Config(_env_file=None))._ws, MassiveWS)


def test_adapter_fetch_ohlcv_mocked(monkeypatch):
    adapter = MassiveAdapter(config=Config(_env_file=None, cache_enabled=False))
    monkeypatch.setattr(
        adapter._rest, "aggs",
        lambda *a, **k: [dict(RAW_AGG)],
    )
    df = adapter.fetch_ohlcv(
        "AAPL", "1h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
    )
    assert len(df) == 1
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume", "session"]


def test_adapter_fetch_ohlcv_invalid_timeframe_raises():
    adapter = MassiveAdapter(config=Config(_env_file=None, cache_enabled=False))
    with pytest.raises(ValueError):
        adapter.fetch_ohlcv(
            "AAPL", "9m", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )


def test_adapter_fetch_ticks_mocked(monkeypatch):
    adapter = MassiveAdapter(config=Config(_env_file=None))
    monkeypatch.setattr(
        adapter._rest, "trades",
        lambda *a, **k: [
            {"t": 1704067200000000000, "p": 40500.0, "s": 2.0, "c": [2]}
        ],
    )
    ticks = adapter.fetch_ticks(
        "AAPL", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
    )
    assert len(ticks) == 1
    assert ticks[0].price == 40500.0
    assert ticks[0].side == "buy"


def test_adapter_fetch_ticks_empty(monkeypatch):
    adapter = MassiveAdapter(config=Config(_env_file=None))
    monkeypatch.setattr(adapter._rest, "trades", lambda *a, **k: [])
    assert adapter.fetch_ticks("AAPL") == []


# --- 6b. Step 2 wiring: cache / persist / output / resample ------------------

def _agg_rows(n: int, start_ms: int, step_ms: int = 3600000, price: float = 100.0) -> list[dict]:
    """Canned closed agg dicts (already closed bars, past timestamps)."""
    return [
        {
            "o": price, "h": price + 1, "l": price - 1, "c": price + 0.5,
            "v": 10.0, "vw": price, "t": start_ms + i * step_ms, "n": 100,
        }
        for i in range(n)
    ]


def _massive_adapter_with_storage(tmp_path) -> MassiveAdapter:
    from datakodo.storage.parquet import ParquetBackend

    return MassiveAdapter(
        storage=ParquetBackend(base_dir=str(tmp_path)),
        config=Config(_env_file=None, cache_enabled=True),
    )


def test_fetch_ohlcv_persist_then_cache_hit(monkeypatch, tmp_path):
    """First call fetches and persists; a second call returns from cache."""
    adapter = _massive_adapter_with_storage(tmp_path)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 1, 4, tzinfo=UTC)
    calls = {"n": 0}

    def _fake_aggs(*a, **k):
        calls["n"] += 1
        return _agg_rows(4, 1704067200000)

    monkeypatch.setattr(adapter._rest, "aggs", _fake_aggs)
    first = adapter.fetch_ohlcv("AAPL", "1h", start, end)
    assert len(first) == 4
    assert calls["n"] == 1

    second = adapter.fetch_ohlcv("AAPL", "1h", start, end)
    assert len(second) == 4
    assert calls["n"] == 1  # cache hit — provider not called again


def test_fetch_ohlcv_force_refresh_bypasses_cache(monkeypatch, tmp_path):
    adapter = _massive_adapter_with_storage(tmp_path)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 1, 4, tzinfo=UTC)
    calls = {"n": 0}

    def _fake_aggs(*a, **k):
        calls["n"] += 1
        return _agg_rows(4, 1704067200000)

    monkeypatch.setattr(adapter._rest, "aggs", _fake_aggs)
    adapter.fetch_ohlcv("AAPL", "1h", start, end)
    assert calls["n"] == 1
    adapter.fetch_ohlcv("AAPL", "1h", start, end, force_refresh=True)
    assert calls["n"] == 2  # provider hit again despite cached data


def test_fetch_ohlcv_persist_disabled_no_write(monkeypatch, tmp_path):
    adapter = MassiveAdapter(
        config=Config(_env_file=None, cache_enabled=True, cache_dir=tmp_path)
    )
    monkeypatch.setattr(
        adapter._rest, "aggs", lambda *a, **k: _agg_rows(4, 1704067200000)
    )
    adapter.fetch_ohlcv(
        "AAPL", "1h",
        datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, 4, tzinfo=UTC),
        persist=False,
    )
    assert not any(tmp_path.rglob("*.parquet"))


def test_fetch_ohlcv_output_format_polars(monkeypatch, tmp_path):
    adapter = _massive_adapter_with_storage(tmp_path)
    monkeypatch.setattr(
        adapter._rest, "aggs", lambda *a, **k: _agg_rows(2, 1704067200000)
    )
    out = adapter.fetch_ohlcv(
        "AAPL", "1h",
        datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, 2, tzinfo=UTC),
        output_format="polars",
    )
    assert type(out).__name__ == "DataFrame"  # polars.DataFrame
    assert out.height == 2


class _MassiveRestricted(MassiveAdapter):
    """Massive adapter pretending to offer only 1h and 1d natively."""

    native_timeframes = (Timeframe.H1, Timeframe.D1)


def test_fetch_ohlcv_resamples_non_native_timeframe(monkeypatch, tmp_path):
    """4h requested, only 1h native -> fetch 1h and resample to one 4h bar."""
    adapter = _MassiveRestricted(config=Config(_env_file=None, cache_enabled=False))
    monkeypatch.setattr(
        adapter._rest, "aggs", lambda *a, **k: _agg_rows(4, 1704067200000, price=100.0)
    )
    df = adapter.fetch_ohlcv(
        "AAPL", "4h",
        datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, 4, tzinfo=UTC),
    )
    assert len(df) == 1
    assert df.loc[0, "open"] == 100.0
    assert df.loc[0, "high"] == 101.0
    assert df.loc[0, "low"] == 99.0
    assert df.loc[0, "close"] == 100.5
    assert df.loc[0, "volume"] == 40.0


def test_fetch_ohlcv_native_timeframe_not_resampled(monkeypatch, tmp_path):
    adapter = _MassiveRestricted(config=Config(_env_file=None, cache_enabled=False))
    captured: dict[str, tuple] = {}

    def _fake_aggs(symbol, multiplier, timespan, start, end, **k):
        captured["mt"] = (multiplier, timespan)
        return _agg_rows(2, 1704067200000)

    monkeypatch.setattr(adapter._rest, "aggs", _fake_aggs)
    df = adapter.fetch_ohlcv(
        "AAPL", "1h",
        datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, 2, tzinfo=UTC),
    )
    assert captured["mt"] == (1, "hour")
    assert len(df) == 2


def test_fetch_ohlcv_all_agg_rows_closed_so_nothing_dropped(monkeypatch, tmp_path):
    """Past bars are fully closed and survive validation (design doc sec 17)."""
    adapter = _massive_adapter_with_storage(tmp_path)
    monkeypatch.setattr(
        adapter._rest, "aggs", lambda *a, **k: _agg_rows(4, 1704067200000)
    )
    df = adapter.fetch_ohlcv(
        "AAPL", "1h",
        datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, 4, tzinfo=UTC),
    )
    assert len(df) == 4


# --- 7. Live integration (needs DATAKODO_MASSIVE_API_KEY in .env) -----------

def test_fetch_ohlcv_live_save_csv(tmp_path):
    """Fetch real daily bars for AAPL and save them to a CSV for inspection.

    Uses the key from the project ``.env``. Skipped when no key is set.
    """
    cfg = Config(_env_file=".env")
    if not cfg.massive_api_key:
        pytest.skip("DATAKODO_MASSIVE_API_KEY not set in .env — skipping live test")

    adapter = MassiveAdapter(config=cfg)
    df = adapter.fetch_ohlcv(
        "AAPL", "1d",
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 2, 1, tzinfo=UTC),
    )
    assert len(df) > 0, "expected at least one daily bar for AAPL in Jan 2025"

    out = Path("tests") / "adapters" / "massive_live_aapl.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\nsaved {len(df)} rows -> {out}")

    assert out.exists()
    assert out.stat().st_size > 0


def test_paid_tier_endpoint_tells_user_to_upgrade():
    """Hit a paid-tier endpoint (v3 trades) and confirm we surface a clear
    upgrade hint instead of a generic HTTP error.

    Historical stock trades (``/v3/trades/{ticker}``) require the Stocks
    Advanced / Business tier (see docs/massive.md §5), so this request is
    expected to fail with ``PaidTierRequiredError`` pointing at the pricing
    page. Skipped when no key is set.
    """
    cfg = Config(_env_file=".env")
    if not cfg.massive_api_key:
        pytest.skip("DATAKODO_MASSIVE_API_KEY not set in .env — skipping live test")

    adapter = MassiveAdapter(config=cfg)
    with pytest.raises(PaidTierRequiredError) as exc_info:
        adapter.fetch_ticks(
            "AAPL", datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 2, tzinfo=UTC)
        )
    assert "upgrade" in str(exc_info.value).lower()
    assert "massive.com" in str(exc_info.value)
