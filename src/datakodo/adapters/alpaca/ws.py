"""Alpaca WebSocket streaming — real-time trade feeds (design doc sec 6/7).

Transport is the official SDK data streams with DataKodo routing, channel
bookkeeping, and error mapping on top. Async generators out, per sec 6.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from alpaca.data.enums import DataFeed
from alpaca.data.live.crypto import CryptoDataStream
from alpaca.data.live.stock import StockDataStream

from datakodo.adapters.alpaca.mapper import trade_of
from datakodo.core.exceptions import AuthenticationError, ConnectionError

logger = logging.getLogger(__name__)

_RECV_TIMEOUT = 60.0


class AlpacaWS:
    """Async trade stream client for Alpaca real-time feeds."""

    def __init__(self, api_key: str = "", secret_key: str = "", feed: str = "iex") -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._feed = feed
        self._kind: str | None = None
        self._stream = None
        self._task: asyncio.Task | None = None
        self._queues: dict[str, asyncio.Queue] = {}
        self._subscribed: set[str] = set()

    @property
    def connected(self) -> bool:
        """Whether a live connection task is running."""
        return self._task is not None and not self._task.done()

    @staticmethod
    def _kind_for(symbol: str) -> str:
        """Stocks vs crypto, same slash rule as the REST paths."""
        return "crypto" if "/" in symbol else "stock"

    def _ensure_stream(self, kind: str):
        """Build the SDK stream for ``kind`` (seam for offline tests)."""
        if not (self._api_key and self._secret_key):
            raise AuthenticationError(
                "Alpaca credentials are missing. Pass api_key/api_secret "
                "explicitly or set APCA_API_KEY_ID / APCA_API_SECRET_KEY."
            )
        if kind == "crypto":
            return CryptoDataStream(self._api_key, self._secret_key)
        return StockDataStream(self._api_key, self._secret_key, feed=DataFeed(self._feed))

    def _require_connection(self):
        if self._stream is None or not self.connected:
            raise ConnectionError("Alpaca WS is not connected; call connect() first.")
        return self._stream

    async def connect(self, kind: str = "stock") -> None:
        """Connect the SDK stream for ``kind`` (design doc sec 23)."""
        await self.disconnect()
        self._kind = kind
        self._queues.clear()
        stream = self._ensure_stream(kind)
        logger.info("Connecting to Alpaca WS kind=%s", kind)
        self._stream = stream
        # _run_forever is SDK-private but the only task-drivable entry point
        # (run() blocks); reconnects stay inside it.
        self._task = asyncio.create_task(stream._run_forever())
        await asyncio.sleep(0)
        if self._task.done() and (exc := self._task.exception()) is not None:
            self._task = None
            self._stream = None
            raise ConnectionError(f"Alpaca WS connection failed: {exc}") from exc

    async def _on_trade(self, trade) -> None:
        """SDK trade callback: route the trade to its symbol's queue."""
        symbol = getattr(trade, "symbol", None)
        if not isinstance(symbol, str):
            logger.debug("Dropping Alpaca trade without a symbol")
            return
        queue = self._queues.get(symbol)
        if queue is None:
            logger.debug("Dropping Alpaca trade for unsubscribed symbol %r", symbol)
            return
        queue.put_nowait(trade)

    async def subscribe(self, symbol: str) -> None:
        """Subscribe to trades for ``symbol`` (idempotent)."""
        stream = self._require_connection()
        if symbol in self._subscribed:
            return
        stream.subscribe_trades(self._on_trade, symbol)
        self._queues.setdefault(symbol, asyncio.Queue())
        self._subscribed.add(symbol)
        logger.info("Alpaca WS subscribed to trades for %s", symbol)

    async def unsubscribe(self, symbol: str) -> None:
        """Unsubscribe from trades for ``symbol``."""
        stream = self._require_connection()
        if symbol not in self._subscribed:
            return
        stream.unsubscribe_trades(symbol)
        self._subscribed.discard(symbol)
        self._queues.pop(symbol, None)
        logger.info("Alpaca WS unsubscribed from trades for %s", symbol)

    async def trade_stream(self, symbol: str) -> AsyncGenerator[Any, None]:
        """Yield canonical ``Trade`` records for ``symbol``.

        Connects (or reconnects on stock/crypto switch) automatically, then
        subscribes. Unmappable frames are skipped, never fatal. An ended
        stream raises instead of going silent. Note: wrong keys idle inside
        the SDK retry loop, so only missing keys fail fast here.
        """
        kind = self._kind_for(symbol)
        if not self.connected or self._kind != kind:
            await self.connect(kind)
        await self.subscribe(symbol)
        queue = self._queues[symbol]
        logger.info("Starting Alpaca trade stream for %s", symbol)
        closed_by_user = False
        try:
            while self.connected:
                try:
                    trade = await asyncio.wait_for(queue.get(), _RECV_TIMEOUT)
                except TimeoutError:
                    if not self.connected:
                        break
                    continue
                try:
                    yield trade_of(trade)
                except (AttributeError, KeyError, TypeError, ValueError) as exc:
                    logger.warning("Skipping unmappable Alpaca trade: %s", exc)
        except GeneratorExit:
            closed_by_user = True
            raise
        finally:
            try:
                if self.connected:
                    await self.unsubscribe(symbol)
            except ConnectionError:
                pass
            logger.info("Stopped Alpaca trade stream for %s", symbol)
        if not closed_by_user:
            raise self._dead_connection_error(symbol)

    def _dead_connection_error(self, symbol: str) -> ConnectionError:
        """Honest error for a stream whose task ended without our asking."""
        exc: BaseException | None = None
        task = self._task
        if task is not None and task.done() and not task.cancelled():
            exc = task.exception()
        return ConnectionError(
            f"Alpaca WS stream for {symbol} ended: connection lost"
            + (f": {exc}" if exc is not None else ".")
        )

    async def disconnect(self) -> None:
        """Close the connection and release all subscriptions (sec 23)."""
        task, self._task = self._task, None
        stream, self._stream = self._stream, None
        self._subscribed.clear()
        self._queues.clear()
        if task is not None:
            if task.done() and not task.cancelled():
                task.exception()
            else:
                task.cancel()
        if stream is not None:
            try:
                await stream.close()
            except Exception as exc:  # noqa: BLE001 - close must not raise
                logger.debug("Alpaca WS close raised: %s", exc)
        logger.info("Alpaca WS disconnected")

    async def __aenter__(self) -> "AlpacaWS":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()
