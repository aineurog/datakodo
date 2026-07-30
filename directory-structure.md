# DataKodo — Directory Structure

```
datakodo/
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
├── .gitignore
│
├── src/
│   └── datakodo/
│       ├── __init__.py              # public API re-exports
│       ├── client.py                # main user-facing Client class
│       ├── config.py                # pydantic-settings config (sec 13)
│       │
│       ├── core/
│       │   ├── __init__.py
│       │   ├── schemas.py           # canonical models: OHLCV, Trade, OrderBook,
│       │   │                         #   Instrument (sec 3, 4, 20)
│       │   ├── instruments.py       # Instrument base + asset-class extensions (sec 4)
│       │   ├── interfaces.py        # abstract base adapter + capability declaration (sec 2)
│       │   ├── exceptions.py        # exception hierarchy (sec 15)
│       │   ├── enums.py             # AssetClass, InstrumentType, Timeframe, Session
│       │   └── timeframe.py         # canonical <-> provider timeframe mapping (sec 19)
│       │
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── binance/
│       │   │   ├── __init__.py
│       │   │   ├── adapter.py       # implements abstract adapter interface
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
│       │   ├── resample.py          # timeframe resampling (sec 7)
│       │   ├── pagination.py        # auto-paginate & stitch (sec 11)
│       │   └── validation.py        # data quality checks: gaps, duplicates,
│       │                             #   monotonic timestamps (sec 18)
│       │
│       ├── ratelimit/
│       │   ├── __init__.py
│       │   └── limiter.py           # token bucket per provider instance (sec 16)
│       │
│       └── storage/
│           ├── __init__.py
│           ├── base.py              # StorageBackend abstract interface (sec 17)
│           └── parquet.py           # Parquet backend (sec 17)
│
├── tests/
│   ├── conftest.py
│   ├── core/
│   │   ├── test_schemas.py          # schema validation as first-class testing (sec 26)
│   │   ├── test_instruments.py
│   │   ├── test_exceptions.py
│   │   └── test_timeframe.py
│   ├── adapters/
│   │   ├── contract_tests.py        # shared contract suite every adapter must pass (sec 26)
│   │   ├── test_binance.py
│   │   ├── test_alpaca.py
│   │   ├── test_polygon.py
│   │   ├── test_mt5.py
│   │   └── test_ibkr.py
│   ├── ops/
│   │   ├── test_resample.py
│   │   ├── test_pagination.py
│   │   └── test_validation.py
│   ├── integration/                 # gated live-API tests, not on every commit (sec 26)
│   └── fixtures/                    # golden recorded API responses per provider (sec 26)
│
├── docs/
│   ├── usage.md
│   └── adapters/                    # per-provider: capabilities, cost tier, limits (sec 29)
│
├── .github/
│   └── workflows/
│       ├── ci.yml                   # lint, type-check, contract tests on every PR
│       └── integration.yml          # gated live-API integration tests
│
├── .pre-commit-config.yaml
└── .ruff.toml
```

## Design Notes

- **`src/` layout** avoids accidental imports of the repo root instead of the installed package.
- **Each adapter is a subpackage**, not a single file. The design doc names five Phase 1 providers (Binance, Alpaca, Polygon, MT5, IBKR — sec 24), and each needs REST, websocket, and mapping logic. A single file per adapter would collapse under that weight.
- **Adapter internal template**: `adapter.py` (implements the interface), `rest.py` (HTTP), `ws.py` (websocket), `mapper.py` (normalization). MT5 has `terminal.py` instead of `rest.py`/`ws.py` because it uses a COM based blocking terminal connection. IBKR uses `client.py` for the TWS callback client.
- **`streaming/`** gives async streaming its own home — websocket reconnect patterns, snapshot+delta order book maintenance. Keeps adapter websocket code thin.
- **`ops/`** groups resample, pagination, and data quality validation. These are cross cutting utilities that serve all adapters but are not part of the core contract.
- **`core/`** is the single source of truth. Schemas, instruments, interfaces, and exceptions live here and nowhere else (sec 2).
- **No `async/` vs `sync/` split** at the package level. The design doc is explicit: `.fetch*()` is sync, `.stream*()` is async, but both live on the same adapter. The split is at the method level, not the package level (sec 5).
- **Tests mirror `src/` structure** so finding the tests for a module is always predictable.

## Tooling

| Tool | Purpose |
|---|---|
| `ruff` | Linting + formatting (replaces flake8, isort, black) |
| `mypy` | Static type checking (critical for typed schemas and asset-class extensions) |
| `pytest` + `pytest-cov` | Test runner with coverage |
| `hypothesis` | Property based testing on normalization functions (sec 26) |
| `pre-commit` | Run linters and formatters before commits |
| `pydantic` + `pydantic-settings` | Schema models and config (sec 13, 20) |
| GitHub Actions CI | Two tiers: contract tests on every commit, integration tests gated |
