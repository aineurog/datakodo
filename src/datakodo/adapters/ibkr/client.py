"""IBKR TWS/Gateway async callback client.

Wraps the ib_insync library which provides a synchronous-style API
on top of the native TWS async callback protocol. Used for historical
data requests (sync path).
"""

import logging
from datetime import datetime

from datakodo.core.exceptions import ConnectionError

logger = logging.getLogger(__name__)


class IBKRClient:
    """Synchronous TWS/Gateway client for historical data requests.

    Uses ib_insync under the hood to manage the event loop and
    callback queue internally. The caller sees a plain sync interface.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._connected = False

    def connect(self) -> None:
        """Connect to the TWS or IB Gateway application."""
        logger.info(
            "Connecting to TWS/Gateway at %s:%d (client_id=%d).",
            self._host,
            self._port,
            self._client_id,
        )
        raise NotImplementedError("IBKR client connection not yet implemented")

    def disconnect(self) -> None:
        """Disconnect from TWS/Gateway."""
        logger.info("Disconnecting from TWS/Gateway.")
        self._connected = False

    def req_historical_data(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list:
        """Request historical OHLCV bars.

        Returns raw IBKR bar list. Mapped to canonical OHLCV by the
        mapper module.
        """
        if not self._connected:
            raise ConnectionError("IBKR client is not connected.")
        raise NotImplementedError("IBKR historical data not yet implemented")
