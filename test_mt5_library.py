"""DataKodo + MetaTrader 5 — end-user usage example.

Shows how a DataKodo user works with MT5 through the public library API
(``from datakodo import Client``) — the same way other providers are used.
Fetched frames are saved as CSVs under ``mt5_csv/`` (single + batch), and
edge-case behavior is demonstrated: symbol search discovers by partial name,
partial symbol names still fail loudly, unloaded history tells you to load the
chart first, and futures contracts are classified.

Requires a **running MetaTrader 5 terminal on Windows**
(``pip install datakodo[mt5]``).

Run:
    python test_mt5_library.py
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from datakodo import Client
from datakodo.core.exceptions import (
    DataNotAvailableError,
    ProviderError,
    SymbolNotFoundError,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

START = datetime.now(UTC) - timedelta(days=7)
END = datetime.now(UTC)
OUT = Path(__file__).parent / "mt5_csv"
BATCH_SYMBOLS = ["EURUSD", "GBPUSD", "XAUUSD"]


def _save(frame, name: str) -> None:
    """Write a fetched frame to ``mt5_csv/<name>`` and report the path."""
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    frame.to_csv(path, index=False)
    print(f"saved -> {path}")


def test_partial_symbol(client: Client) -> None:
    """Partial symbol names fail loudly — MT5 has no client-side search.

    ``symbol_select("EUR")`` returns False for an unrecognized name, which the
    adapter surfaces as ``SymbolNotFoundError`` (design doc sec 16).
    """
    try:
        client.fetch_ohlcv("EUR", "1h", START, END)
    except SymbolNotFoundError as exc:
        print(f"partial symbol 'EUR' rejected as expected: {exc}")
    else:
        raise AssertionError("expected SymbolNotFoundError for partial symbol 'EUR'")


def test_symbol_search(client: Client, query: str = "EUR") -> None:
    """Discovery by partial name — the design's search-then-fetch flow (sec 5).

    ``search_instruments(query)`` matches a case-insensitive substring across
    the terminal's full symbol universe (fetched once via ``symbols_get``) and
    returns canonical ``Instrument`` descriptors. ``quote``, ``asset_class``,
    ``instrument_type``, and ``exchange`` filter further; the found symbols
    then feed ``fetch_ohlcv_batch`` so discovery to fetch is one flow.
    """
    results = client.search_instruments(query, limit=10)
    symbols = [r.symbol for r in results]
    print(f"search_instruments({query!r}) -> {len(results)} result(s): {symbols}")
    if not results:
        print(f"  no symbols match {query!r}")
        return
    for inst in results[:3]:
        shown = (
            f"{inst.asset_class.value} / {inst.instrument_type.value} ({inst.exchange or 'MT5'})"
        )
        print(f"  {inst.symbol}: {shown}")

    top = symbols[:3] if len(results) > 3 else symbols
    for symbol in top:
        try:
            df = client.fetch_ohlcv(symbol, "1h", START, END)
            print(f"  {symbol}: {len(df)} bars")
        except DataNotAvailableError as exc:
            print(f"  {symbol}: history still loading - open its chart in MT5 ({exc})")


def test_history_loading(client: Client, symbol: str = "XAUUSD") -> None:
    """Tell the user to load history when the terminal has no bars yet.

    ``fetch_ohlcv`` already calls ``symbol_select``, which kicks off the
    terminal's history download. If the window still comes back empty, the
    library logs a warning and raises ``DataNotAvailableError`` (rest.py).
    Open the chart (or raise 'Max. bars in chart') and re-run.
    """
    try:
        df = client.fetch_ohlcv(symbol, "1h", START, END)
    except DataNotAvailableError:
        print(
            f"No history loaded yet for {symbol!r}: data is downloading in the "
            "background - this may take some time. Open the {symbol} chart in "
            "MetaTrader 5 (or raise 'Max. bars in chart') to speed it up, then "
            "re-run."
        )
    else:
        print(f"{symbol} history loaded: {len(df)} bars")


def test_futures(client: Client, symbol: str = "DXY_U6") -> None:
    """Futures contracts (IC Markets) are detected and fetchable.

    ``DXY_U6`` is the US Dollar Index futures contract (September 2026
    expiry, so the suffix is year 2026 / month U = September). The adapter
    classifies from ``trade_calc_mode`` + ``path`` in ``symbol_info`` (design
    doc sec 4), so futures need no hint — ``market_type="futures"`` only
    validates against the detection.
    """
    inst = client.instrument(symbol, market_type="futures")
    print(f"futures instrument: {inst.asset_class.value} / {inst.instrument_type.value}")
    if inst.future is not None:
        print(
            f"  expiry={inst.future.expiry}, contract_size={inst.future.contract_size}, "
            f"tick_size={inst.future.tick_size}, multiplier={inst.future.multiplier}"
        )
    df = client.fetch_ohlcv(symbol, "1h", START, END)
    print(f"{symbol} 1h: {len(df)} bars, last close={df['close'].iloc[-1]}")
    _save(df, f"{symbol.lower()}_1h_futures.csv")


def main() -> None:
    try:
        with Client("mt5") as client:
            # df = client.fetch_ohlcv("EURUSD", "1h", START, END)
            # print("1h OHLCV (canonical base columns):")
            # print(df.head().to_string())
            # print(f"-> {len(df)} bars, columns: {list(df.columns)}")
            # _save(df, "eurusd_1h_single.csv")

            # inst = client.instrument("EURUSD", market_type="spot")
            # print(f"instrument: {inst.asset_class.value} / {inst.instrument_type.value}")

            # f = client.fetch_fundamentals("EURUSD")
            # print(f"fundamentals: name={f.name}, currency={f.currency}, as_of={f.as_of}")

            # live = client.fetch_ohlcv("EURUSD", "1h", START, END, include_live=True)
            # print(f"include_live: {int((~live['is_closed']).sum())} still-forming bar(s)")
            # _save(live, "eurusd_1h_live.csv")

            # res = client.fetch_ohlcv_batch(BATCH_SYMBOLS, "1h", START, END)
            # print(f"batch: {[len(v) for v in res.values()]} bars each for {list(res)}")
            # for symbol, frame in res.items():
            #     _save(frame, f"{symbol.lower()}_1h_batch.csv")

            # combined = client.fetch_ohlcv_batch(BATCH_SYMBOLS, "1h", START, END, combine=True)
            # print(
            #     f"combined: {len(combined)} rows, symbol column: "
            #     f"{'symbol' in combined.columns}"
            # )
            # _save(combined, "batch_1h_combined.csv")

            test_partial_symbol(client)
            test_symbol_search(client, "EUR")
            test_history_loading(client, "XAUUSD")
            test_futures(client, "DXY_U6")
    except ProviderError as exc:
        if "Out of memory" not in str(exc):
            raise
        print(
            "\nThe MT5 terminal itself is out of memory - this is NOT a symbol error. "
            "\nFix: close unused charts, remove symbols from MarketWatch "
            "\n(MarketWatch -> right-click -> Remove), and restart the terminal, "
            "\nthen re-run."
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
