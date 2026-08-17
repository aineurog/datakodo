"""MT5 library check — imports datakodo and tests each thing on live terminal data.

Requires a running MetaTrader 5 terminal on Windows (pip install datakodo[mt5]).

Run:
    python test_mt5_library.py

Also writes verification CSVs with **UTC** timestamps so bars can be cross-checked
against the broker's MT5 terminal / exchange chart:

    mt5_csv/EURUSD_1m_utc.csv   raw M1 bars (finest granularity)
    mt5_csv/EURUSD_1h_utc.csv   H1 bars (adapter fetch_ohlcv)

MT5 labels its charts in **server time**; DataKodo stores **UTC** (design doc
sec 10). The server offset is measured live and subtracted so the saved CSVs
are true UTC, matching what the exchange would show when converted to UTC.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from datakodo.adapters.mt5.adapter import MT5Adapter
from datakodo.adapters.mt5.mapper import map_ohlcv
from datakodo.adapters.mt5.terminal import MT5Terminal

START = datetime.now(UTC) - timedelta(days=7)
END = datetime.now(UTC)

CSV_DIR = Path(__file__).parent / "mt5_csv"


def save_csv(df: pd.DataFrame, filename: str) -> Path:
    """Write *df* to :data:`CSV_DIR` with timestamps formatted as explicit UTC.

    Rows keep their canonical ``timestamp`` (bar open time, tz-aware UTC);
    ``date_format`` renders it as ``YYYY-MM-DD HH:MM:SS+00:00`` so the CSV is
    unambiguous regardless of spreadsheet locale.
    """
    CSV_DIR.mkdir(exist_ok=True)
    out = df.copy()
    if "timestamp" in out.columns:
        ts = out["timestamp"]
        if ts.dt.tz is not None:
            ts = ts.dt.tz_convert("UTC")
        else:
            ts = ts.dt.tz_localize("UTC")
        out["timestamp"] = ts
    path = CSV_DIR / filename
    out.to_csv(path, index=False, date_format="%Y-%m-%d %H:%M:%S+00:00")
    print(f"saved {path} ({len(out)} rows)")
    return path


def _server_offset(symbol: str) -> int:
    """Measure the terminal's server-vs-UTC offset for *symbol* (seconds)."""
    term = MT5Terminal()
    term.initialize()
    try:
        return term.server_offset_seconds(symbol)
    finally:
        term.shutdown()


def _correct_server_time(df: pd.DataFrame, offset_seconds: int) -> pd.DataFrame:
    """Shift server-time-as-UTC bars to true UTC.

    Until Step 4 wires ``offset_seconds`` into ``MT5Adapter.fetch_ohlcv`` the
    adapter maps raw server-time epochs to UTC without shifting. Undo that here
    using the measured offset so saved CSVs are genuinely UTC.
    """
    out = df.copy()
    out["timestamp"] = out["timestamp"] - pd.Timedelta(seconds=offset_seconds)
    return out


def test_terminal_connect():
    term = MT5Terminal()
    assert term.initialize() is True
    print(
        f"connected={term.connected}, "
        f"server_offset_seconds(EURUSD)={term.server_offset_seconds('EURUSD')}"
    )
    return term


def test_copy_rates_range(term):
    """Fetch raw M1 bars and save them (server time → UTC) right away.

    M1 is the finest granularity MT5 serves, so it is the easiest to eyeball
    against the broker's chart: for every M1 bar the CSV open time (UTC) is
    server-open-time minus the measured server offset. This must run on the
    first request after connect — MT5 only returns history it has buffered
    for the symbol, and later requests of the same session often come back
    empty.
    """
    raw = term.copy_rates_range("EURUSD", 1, START, END)
    assert raw is not None and len(raw) > 0
    print(f"{len(raw)} raw M1 bars, dtype fields: {list(raw.dtype.names)}")
    offset = term.server_offset_seconds("EURUSD")
    df = map_ohlcv(raw, offset_seconds=offset)
    save_csv(df, "EURUSD_1m_utc.csv")
    return term


def test_symbol_info(term):
    info = term.symbol_info("EURUSD")
    assert info is not None
    print(
        f"EURUSD path={getattr(info, 'path', '?')}, "
        f"calc_mode={getattr(info, 'trade_calc_mode', '?')}, "
        f"digits={getattr(info, 'digits', '?')}"
    )
    return term


def test_calc_modes(term):
    print(
        f"futures_calc_modes={sorted(term.futures_calc_modes())}, "
        f"forex_calc_modes={sorted(term.forex_calc_modes())}"
    )
    return term


# Adapter takes canonical strings ("1h") and maps via core.timeframe.MT5_MAP.


def test_client_ohlcv():
    with MT5Adapter() as adapter:
        df = adapter.fetch_ohlcv("EURUSD", "1h", START, END)
        offset = _server_offset("EURUSD")
    print(df.head().to_string())
    assert len(df) > 0
    print(f"{len(df)} bars, columns: {list(df.columns)}")

    save_csv(_correct_server_time(df, offset), "EURUSD_1h_utc.csv")


def test_client_include_live():
    with MT5Adapter() as adapter:
        df = adapter.fetch_ohlcv("EURUSD", "1h", START, END, include_live=True)
    assert len(df) > 0
    print(f"{len(df)} bars (include_live=True)")


def test_client_columns_all():
    with MT5Adapter() as adapter:
        df = adapter.fetch_ohlcv("EURUSD", "1h", START, END, columns="all")
    print(f"columns='all': {list(df.columns)}")


def test_client_batch():
    with MT5Adapter() as adapter:
        res = adapter.fetch_ohlcv_batch(["EURUSD", "GBPUSD"], "1h", START, END)
    print(f"batch: {[len(df) for df in res.values()]} bars each for {list(res)}")


def test_client_bad_symbol():
    with MT5Adapter() as adapter:
        df = adapter.fetch_ohlcv("NOT_A_SYMBOL", "1h", START, END)
    # Step 6 wires this to SymbolNotFoundError; until then it returns empty.
    print(f"unknown symbol -> {len(df)} bars (empty; error mapping lands in Step 6)")


if __name__ == "__main__":
    term = test_terminal_connect()
    term = test_copy_rates_range(term)
    term = test_symbol_info(term)
    term = test_calc_modes(term)
    test_client_ohlcv()
    test_client_include_live()
    test_client_columns_all()
    test_client_batch()
    test_client_bad_symbol()
    term.shutdown()
    print(f"CSVs written to: {CSV_DIR.resolve()}")
    print("done")
