"""Async generator patterns and reconnect logic for streaming adapters.

Each adapter's websocket layer delegates reconnect and backoff to these
utilities so every provider gets consistent, well-tested reconnect behavior.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def with_reconnect(
    connect_fn,
    stream_fn,
    *,
    max_retries: int = 0,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
) -> AsyncGenerator[T, None]:
    """Wrap an async streaming source with automatic reconnect and backoff.

    *connect_fn* is called to establish (or re-establish) a connection.
    It should return a connection object that is passed to *stream_fn*.
    *stream_fn* must be an async generator that yields messages from
    the connection.

    If the stream ends or raises an error, reconnect is attempted with
    exponential backoff. Set *max_retries* to 0 for infinite retries.
    """
    attempt = 0
    delay = base_delay

    while max_retries == 0 or attempt <= max_retries:
        try:
            conn = await connect_fn()
            logger.info("Streaming connection established (attempt %d).", attempt + 1)
            attempt = 0
            delay = base_delay

            async for item in stream_fn(conn):
                yield item

        except asyncio.CancelledError:
            logger.info("Streaming cancelled.")
            return

        except Exception:
            attempt += 1
            if max_retries > 0 and attempt > max_retries:
                logger.error("Max retries (%d) exhausted.", max_retries)
                raise

            logger.warning(
                "Stream disconnected. Reconnecting in %.1fs (attempt %d)...",
                delay,
                attempt,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)


async def amerge(*generators: AsyncGenerator) -> AsyncGenerator:
    """Merge multiple async generators into a single stream.

    Each yielded item is a tuple of ``(generator_index, value)`` so the
    consumer can distinguish which source produced the message.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def _feed(idx: int, gen: AsyncGenerator) -> None:
        try:
            async for value in gen:
                await queue.put((idx, value))
        finally:
            await queue.put((idx, StopAsyncIteration))

    tasks = [asyncio.create_task(_feed(i, gen)) for i, gen in enumerate(generators)]

    try:
        finished = 0
        while finished < len(tasks):
            idx, value = await queue.get()
            if value is StopAsyncIteration:
                finished += 1
            else:
                yield idx, value
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
