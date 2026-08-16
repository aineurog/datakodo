# DataKodo — Directory Structure

```
datakodo/
├── pyproject.toml                   # project metadata + provider extras (sec 25)
├── README.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
├── .gitignore
│
├── src/
│   └── datakodo/
│       ├── __init__.py              # public API re-exports (Config re-exported from core)
│       ├── client.py                # main user-facing Client class
│       ├── py.typed                 # PEP 561 marker — signals inline type hints to mypy
│       │
│       ├── core/
│       │   ├── __init__.py
│       │   ├── config.py            # pydantic-settings config (sec 13)
│       │   ├── schemas.py           # canonical models: OHLCV, Trade, OrderBook,
│       │   │                         #   Instrument (sec 3, 4, 20)
│       │   ├── instruments.py       # Instrument base + asset-class extensions (sec 4)
│       │   ├── interfaces.py        # AdapterInterface + capability checks
│       │   │                         #   (check_capability, NotSupportedError,
│       │   │                         #   PaidTierRequiredError) (sec 2)
│       │   ├── exceptions.py        # exception hierarchy (sec 15)
│       │   ├── enums.py             # AssetClass, InstrumentType, Timeframe, Session
│       │   └── timeframe.py         # canonical <-> provider timeframe mapping (sec 19)
│       │
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── binance/
│       │   │   ├── __init__.py
│       │   │   ├── adapter.py       # implements abstract adapter interface
│       │   │   ├── config.py        # provider-specific settings (BinanceConfig)
│       │   │   ├── rest.py          # HTTP client
│       │   │   ├── ws.py            # websocket streaming
│       │   │   └── mapper.py        # raw provider response -> canonical schema
│       │   ├── alpaca/
│       │   │   ├── __init__.py
│       │   │   ├── adapter.py
│       │   │   ├── rest.py
│       │   │   ├── ws.py
│       │   │   └── mapper.py
│       │   ├── polygon/
│       │   │   ├── __init__.py
│       │   │   ├── adapter.py
│       │   │   ├── rest.py
│       │   │   ├── ws.py
│       │   │   └── mapper.py
│       │   ├── mt5/
│       │   │   ├── __init__.py
│       │   │   ├── adapter.py
│       │   │   ├── terminal.py      # COM-based terminal connection (blocking)
│       │   │   └── mapper.py
│       │   └── ibkr/
│       │       ├── __init__.py
│       │       ├── adapter.py
│       │       ├── client.py        # TWS/Gateway async callback client
│       │       ├── ws.py
│       │       └── mapper.py
│       │
│       ├── streaming/
│       │   ├── __init__.py
│       │   ├── base.py              # async generator patterns, reconnect logic
│       │   └── orderbook.py         # snapshot + delta -> maintained L2 book
│       │
│       ├── ops/
│       │   ├── __init__.py
│       │   ├── output.py            # pandas/polars/arrow output conversion (sec 13)
│       │   ├── resample.py          # timeframe resampling (sec 7)
│       │   ├── pagination.py        # auto-paginate & stitch (sec 11)
│       │   ├── validation.py        # data quality checks: gaps, duplicates,
│       │   │                         #   monotonic timestamps, is_closed (sec 18)
│       │   └── corporate_actions.py # split/dividend adjustment logic (sec 18)
│       │
│       └── ratelimit/
│           ├── __init__.py
│           └── limiter.py           # token bucket per provider instance (sec 16)
│
├── tests/
│   ├── conftest.py
│   ├── test_library_imports.py      # imports every public module (sec 26)
│   ├── core/
│   │   ├── test_schemas.py          # schema validation as first-class testing (sec 26)
│   │   ├── test_instruments.py
│   │   ├── test_exceptions.py
│   │   └── test_timeframe.py
│   ├── adapters/
│   │   ├── contract_tests.py        # shared contract suite every adapter must pass (sec 26)
│   │   ├── test_binance_components.py
│   │   ├── test_binance_config.py
│   │   ├── test_binance_contract.py
│   │   ├── test_binance_fetch.py    # live fetch script (manual run)
│   │   ├── test_binance_live.py     # live WebSocket/REST check (manual run)
│   │   ├── test_alpaca.py
│   │   ├── test_polygon.py
│   │   ├── test_mt5.py
│   │   └── test_ibkr.py
│   ├── ops/
│   │   ├── test_output.py
│   │   ├── test_resample.py
│   │   ├── test_pagination.py
│   │   ├── test_validation.py
│   │   └── test_corporate_actions.py
│   ├── integration/                 # gated live-API tests, not on every commit (sec 26)
│   └── fixtures/                    # golden recorded API responses per provider (sec 26)
│
├── docs/
│   ├── usage.md
│   └── adapters/                    # per-provider: capabilities, cost tier, limits (sec 29)
│
└── .github/
    └── workflows/
        ├── ci.yml                   # lint, type-check, contract tests on every push
        └── integration.yml          # gated live-API integration tests
```

## Design Notes

- **`src/` layout** avoids accidental imports of the repo root instead of the installed package.
- **`py.typed`** is an empty marker file per PEP 561 that tells mypy and downstream type checkers this package ships inline type annotations.
- **`config.py` lives in `core/`** and holds only cross-cutting settings (output format, retries, resampling flag, log level). Provider-specific settings live on each adapter's own config model (e.g. `BinanceConfig`), so the global config never grows a field per provider. The top level `__init__.py` re-exports `Config` so `from datakodo.config import Config` still works.
- **`core/interfaces.py`** holds the `AdapterInterface` abstract base class plus the shared plumbing adapters inherit: `symbol_of()`, the default `fetch_ohlcv_batch()`, `search_instruments()`, lifecycle methods, and capability checking (`check_capability()` that raises `NotSupportedError`/`PaidTierRequiredError`).
- **`ops/output.py`** is the single conversion point from the internal frame to the user-facing format (`pandas` default, `polars`, or `arrow`), so adapters never hand back raw provider data.
- **`ops/corporate_actions.py`** is separate from `validation.py` since splits/dividends handling is non-trivial adjustment logic, not just a sanity check. Contains `adjust_ohlcv_for_splits(df, split_history)` and `adjust_ohlcv_for_dividends(df, dividend_history)`.
- **Each adapter is a subpackage**, not a single file. The design doc names five Phase 1 providers (Binance, Alpaca, Polygon, MT5, IBKR — sec 24), and each needs REST, websocket, and mapping logic. A single file per adapter would collapse under that weight.
- **Adapter internal template**: `adapter.py` (implements the interface), `rest.py` (HTTP), `ws.py` (websocket), `mapper.py` (normalization). MT5 has `terminal.py` instead of `rest.py`/`ws.py` because it uses a COM based blocking terminal connection. IBKR uses `client.py` for the TWS callback client.
- **`streaming/`** gives async streaming its own home — websocket reconnect patterns, snapshot+delta order book maintenance. Keeps adapter websocket code thin.
- **`ops/`** groups resample, pagination, data quality validation, and corporate actions adjustments. These are cross cutting utilities that serve all adapters but are not part of the core contract.
- **`core/`** is the single source of truth. Schemas, instruments, interfaces, exceptions, and config live here and nowhere else (sec 2).
- **No `async/` vs `sync/` split** at the package level. The design doc is explicit: `.fetch*()` is sync, `.stream*()` is async, but both live on the same adapter. The split is at the method level, not the package level (sec 5).
- **Tests mirror `src/` structure** so finding the tests for a module is always predictable.

## Tooling

| Tool | Purpose |
|---|---|
| `ruff` | Linting + formatting (replaces flake8, isort, black) |
| `mypy` | Static type checking (critical for typed schemas and asset-class extensions) |
| `pytest` + `pytest-cov` | Test runner with coverage |
| `hypothesis` | Property based testing on normalization functions (sec 26) |
| `pydantic` + `pydantic-settings` | Schema models and config (sec 13, 20) |
| GitHub Actions CI | Two tiers: contract tests on every commit, integration tests gated |
