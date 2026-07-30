"""MT5 COM-based terminal connection (blocking).

MetaTrader 5 Python package provides a natively blocking API.
This module wraps the initialization, shutdown, and data retrieval
calls behind a clean interface.
"""

import logging
from datetime import datetime

from datakodo.core.exceptions import ConnectionError

logger = logging.getLogger(__name__)


class MT5Terminal:
    """Wraps the MetaTrader 5 terminal connection.

    All methods are synchronous/blocking. The MT5 Python package
    requires a running MT5 terminal on the same machine (Windows).
    """

    def __init__(self, terminal_path: str = "") -> None:
        self._path = terminal_path
        self._connected = False

    def initialize(self) -> bool:
        """Connect to the MT5 terminal.

        Returns True if the connection succeeded.
        """
        logger.info(
            "Initializing MT5 terminal connection (path=%s).",
            self._path or "<default>",
        )
        raise NotImplementedError("MT5 terminal connection not yet implemented")

    def shutdown(self) -> None:
        """Close the MT5 terminal connection."""
        logger.info("Shutting down MT5 terminal connection.")
        self._connected = False

    def copy_rates_range(self, symbol: str, timeframe: str, start: datetime, end: datetime):
        """Fetch OHLCV rates for *symbol* over the given date range.

        Returns a numpy structured array (MT5 native format).
        Mapped to canonical OHLCV by the mapper module.
        """
        if not self._connected:
            raise ConnectionError("MT5 terminal is not connected.")
        raise NotImplementedError("MT5 data retrieval not yet implemented")
