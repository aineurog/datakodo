"""Validate the project can be used as a library.

Imports every public module and instantiates representative top-level
objects to confirm there are no import or initialization errors.
"""

import importlib

import pytest

from datakodo.adapters.binance.adapter import BinanceAdapter
from datakodo.adapters.binance.mapper import map_ohlcv, map_trades
from datakodo.adapters.binance.rest import BinanceREST
from datakodo.core.config import Config
from datakodo.core.enums import Timeframe
from datakodo.ratelimit.limiter import TokenBucket

# Every public module in the installed package.
PUBLIC_MODULES = [
    "datakodo",
    "datakodo.core",
    "datakodo.core.config",
    "datakodo.core.enums",
    "datakodo.core.exceptions",
    "datakodo.core.instruments",
    "datakodo.core.interfaces",
    "datakodo.core.schemas",
    "datakodo.core.timeframe",
    "datakodo.client",
    "datakodo.ops",
    "datakodo.ops.corporate_actions",
    "datakodo.ops.pagination",
    "datakodo.ops.resample",
    "datakodo.ops.validation",
    "datakodo.ratelimit",
    "datakodo.ratelimit.limiter",
    "datakodo.storage",
    "datakodo.storage.base",
    "datakodo.storage.cache",
    "datakodo.storage.parquet",
    "datakodo.streaming",
    "datakodo.streaming.base",
    "datakodo.streaming.orderbook",
    "datakodo.adapters.binance",
    "datakodo.adapters.binance.adapter",
    "datakodo.adapters.binance.mapper",
    "datakodo.adapters.binance.rest",
    "datakodo.adapters.binance.ws",
    "datakodo.adapters.alpaca",
    "datakodo.adapters.alpaca.adapter",
    "datakodo.adapters.alpaca.mapper",
    "datakodo.adapters.alpaca.rest",
    "datakodo.adapters.alpaca.ws",
    "datakodo.adapters.polygon",
    "datakodo.adapters.polygon.adapter",
    "datakodo.adapters.polygon.mapper",
    "datakodo.adapters.polygon.rest",
    "datakodo.adapters.polygon.ws",
    "datakodo.adapters.ibkr",
    "datakodo.adapters.ibkr.adapter",
    "datakodo.adapters.ibkr.client",
    "datakodo.adapters.ibkr.mapper",
    "datakodo.adapters.ibkr.ws",
    "datakodo.adapters.mt5",
    "datakodo.adapters.mt5.adapter",
    "datakodo.adapters.mt5.mapper",
    "datakodo.adapters.mt5.terminal",
]


@pytest.mark.parametrize("module", PUBLIC_MODULES)
def test_module_imports(module):
    assert importlib.import_module(module) is not None


def test_representative_objects_initialize():
    # No init/import errors for common public entry points.
    config = Config()
    assert config is not None
    assert Timeframe("1h") is Timeframe.H1
    assert TokenBucket(rate=1.0, burst=5) is not None
    assert BinanceREST() is not None
    assert BinanceAdapter() is not None
    assert map_ohlcv([]).empty
    assert map_trades({"T": 1704067200000, "p": "1.0", "q": "1.0", "m": True}).price == 1.0
