"""Massive WebSocket streaming — real-time trade feeds.

Async-native live ticks (design doc sec 6: ``.stream*()`` are always async
generators; sec 7: streaming is the native use case for ticks). Transport
comes from the official ``massive.WebSocketClient`` (reconnects included);
DataKodo adds symbol-prefixed market routing, channel bookkeeping, status
filtering, and mapping of auth failures onto the DataKodo hierarchy
(design doc sec 16). Structured logging only, no prints (sec 21).
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from massive.websocket import AuthError, WebSocketClient

from datakodo.core.exceptions import AuthenticationError, ConnectionError, DataLibError

logger = logging.getLogger(__name__)

# Provider symbol prefix -> official market name (mirrors adapter._market_of,
# with the official ``indices`` spelling).
_MARKET_BY_PREFIX = {
    "X:": "crypto",
    "C:": "forex",
    "I:": "indices",
    "O:": "options",
}

# Seconds without a frame before the stream health-checks the connection.
_RECV_TIMEOUT = 60.0


class MassiveWS:
    """Async trade stream client for Massive.com real-time feeds."""

    def __init__(self, api_key: str = "", market: str = "stocks", max_reconnects: int = 5) -> None:
        self._api_key = api_key
        self._market = market
        self._max_reconnects = max_reconnects
        self._client: WebSocketClient | None = None
        self._task: asyncio.Task | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._subscribed: set[tuple[str, str]] = set()

    @property
    def connected(self) -> bool:
        """Whether a live connection task is running."""
        return self._task is not None and not self._task.done()

    @staticmethod
    def _market_for(symbol: str) -> str:
        """Infer the official market name from a provider symbol prefix."""
        upper = symbol.upper()
        for prefix, market in _MARKET_BY_PREFIX.items():
            if upper.startswith(prefix):
                return market
        return "stocks"

    async def connect(self, market: str = "stocks") -> None:
        """Connect to the Massive feed for ``market`` (design doc sec 23).

        Closes any existing connection first. An invalid key surfaces promptly
        as ``AuthenticationError`` instead of failing silently later.
        """
        await self.disconnect()
        self._market = market
        self._client = WebSocketClient(
            api_key=self._api_key or None,
            market=market,
            raw=True,
            max_reconnects=self._max_reconnects,
        )
        logger.info("Connecting to Massive WS market=%s", market)
        self._task = asyncio.create_task(self._client.connect(self._on_message))
        await asyncio.sleep(0)
        if self._task.done() and (exc := self._task.exception()) is not None:
            self._task = None
            self._client = None
            if isinstance(exc, AuthError):
                raise AuthenticationError(f"Massive WS authentication failed: {exc}") from exc
            raise ConnectionError(f"Massive WS connection failed: {exc}") from exc

    async def _on_message(self, raw: str | bytes) -> None:
        """Official-client callback: decode one raw frame into queued dicts.

        Async because the official client ``await``s the handler.
        """
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            payload = json.loads(text)
        except (UnicodeDecodeError, ValueError):
            logger.warning("Ignoring non-JSON Massive WS frame")
            return
        messages = payload if isinstance(payload, list) else [payload]
        for message in messages:
            if isinstance(message, dict):
                self._queue.put_nowait(message)

    def _require_connection(self) -> WebSocketClient:
        if self._client is None or not self.connected:
            raise ConnectionError("Massive WS is not connected; call connect() first.")
        return self._client

    @staticmethod
    async def _send(client: WebSocketClient, method: str, subscription: str) -> None:
        """Call the official client's (un)subscribe, sync or async alike."""
        result = getattr(client, method)(subscription)
        if asyncio.iscoroutine(result):
            await result

    async def subscribe(self, channel: str, symbol: str) -> None:
        """Subscribe to ``channel`` (e.g. ``T``) for ``symbol``."""
        client = self._require_connection()
        key = (channel, symbol)
        if key in self._subscribed:
            return
        await self._send(client, "subscribe", f"{channel}.{symbol}")
        self._subscribed.add(key)
        logger.info("Massive WS subscribed to %s.%s", channel, symbol)

    async def unsubscribe(self, channel: str, symbol: str) -> None:
        """Unsubscribe from ``channel`` for ``symbol``."""
        client = self._require_connection()
        key = (channel, symbol)
        if key not in self._subscribed:
            return
        await self._send(client, "unsubscribe", f"{channel}.{symbol}")
        self._subscribed.discard(key)
        logger.info("Massive WS unsubscribed from %s.%s", channel, symbol)

    async def trade_stream(self, symbol: str) -> AsyncGenerator[Any, None]:
        """Yield canonical ``Trade`` records for ``symbol`` (trades channel).

        Connects (or reconnects) to the symbol's market automatically, then
        subscribes ``T``. Status/metadata frames are skipped; unmappable
        frames are logged and skipped; the ``T`` subscription is released on
        exit. A dead connection surfaces as ``ConnectionError``, never
        silence (design doc sec 2).
        """
        from datakodo.adapters.massive.mapper import map_trades

        market = self._market_for(symbol)
        if not self.connected or self._market != market:
            await self.connect(market)
        await self.subscribe("T", symbol)
        logger.info("Starting Massive trade stream for %s", symbol)
        closed_by_user = False
        try:
            while self.connected:
                try:
                    message = await asyncio.wait_for(self._queue.get(), _RECV_TIMEOUT)
                except TimeoutError:
                    if not self.connected:
                        break
                    continue
                if not isinstance(message, dict) or message.get("ev") == "status":
                    continue
                try:
                    yield map_trades(message)
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning("Skipping unmappable Massive trade: %s", exc)
        except GeneratorExit:
            closed_by_user = True
            raise
        finally:
            try:
                if self.connected:
                    await self.unsubscribe("T", symbol)
            except ConnectionError:
                pass
            logger.info("Stopped Massive trade stream for %s", symbol)
        if not closed_by_user:
            raise self._dead_connection_error(symbol)

    def _dead_connection_error(self, symbol: str) -> DataLibError:
        """Build the honest error for a stream that ended on a dead task."""
        exc: BaseException | None = None
        task = self._task
        if task is not None and task.done() and not task.cancelled():
            exc = task.exception()
        if isinstance(exc, AuthError):
            return AuthenticationError(f"Massive WS authentication failed for {symbol}: {exc}")
        return ConnectionError(
            f"Massive WS stream for {symbol} ended: connection lost"
            + (f": {exc}" if exc is not None else ".")
        )

    async def stream_trades(self, symbol: str) -> AsyncGenerator[Any, None]:
        """Alias for ``trade_stream`` (most common use case: trade ticks)."""
        async for trade in self.trade_stream(symbol):
            yield trade

    async def disconnect(self) -> None:
        """Close the connection and release all subscriptions (sec 23)."""
        task, self._task = self._task, None
        client, self._client = self._client, None
        self._subscribed.clear()
        if task is not None:
            if task.done() and not task.cancelled():
                task.exception()  # consume; a dead connection already surfaced
            else:
                task.cancel()
        if client is not None:
            try:
                await client.close()
            except Exception as exc:  # noqa: BLE001 - close must not raise
                logger.debug("Massive WS close raised: %s", exc)
        logger.info("Massive WS disconnected")

    async def __aenter__(self) -> "MassiveWS":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()
