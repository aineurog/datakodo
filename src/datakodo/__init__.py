"""DataKodo — unified market data library."""

import sys

from datakodo.client import Client
from datakodo.core import config as _config
from datakodo.core.config import Config

# Register so "from datakodo.config import Config" works.
sys.modules["datakodo.config"] = _config

__all__ = ["Client", "Config"]
