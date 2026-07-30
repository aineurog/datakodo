# Contributing to DataKodo

Thank you for your interest in contributing.

## Getting Started

1. Clone the repository and install in editable mode:

```bash
pip install -e ".[all,dev]"
```

2. Run the test suite to confirm everything works:

```bash
pytest tests/
```

## Adding a New Provider Adapter

Every new adapter must follow the pattern established by the five Phase 1
adapters (Binance, Alpaca, Polygon, MT5, IBKR). An adapter subpackage
lives under `src/datakodo/adapters/<provider>/` and contains:

- `adapter.py` — implements `AdapterInterface` from `datakodo.core.interfaces`
- `rest.py` — HTTP client (if the provider has a REST API)
- `ws.py` — WebSocket client (if the provider supports streaming)
- `mapper.py` — converts raw provider responses into the canonical schema

The new adapter must pass the shared contract test suite defined in
`tests/adapters/contract_tests.py`.

## Code Quality

- All code is formatted with `ruff format` and linted with `ruff check`.
- Static type checking with `mypy` (strict mode for new modules).
- Tests use `pytest`. Aim for meaningful coverage of normalization logic
  and edge cases.
- Canonical schemas are the single source of truth. Adapters conform to
  them and never redefine their own versions.

## Pull Request Process

1. Run `ruff check src/ tests/` and `mypy src/datakodo/` before submitting.
2. Ensure `pytest tests/` passes with the existing test suite.
3. New adapters must include contract test parametrization.
4. PRs are reviewed against the design document in `project-doc/design-document.md`.
