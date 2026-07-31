"""BackpressureQueue: bounded asyncio queue with non-blocking try_put."""

from __future__ import annotations

import asyncio
from typing import Any


class BackpressureQueue:
    """Bounded queue with non-blocking put."""

    def __init__(self, *, maxsize: int = 1000) -> None:
        if maxsize <= 0:
            raise ValueError(f"maxsize must be > 0 (got {maxsize})")
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self._maxsize = maxsize

    def try_put(self, item: Any) -> bool:
        """Non-blocking enqueue. Returns False if the queue is full."""
        try:
            self._queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            return False

    def size(self) -> int:
        """Return the current number of items in the queue."""
        return self._queue.qsize()

    def maxsize(self) -> int:
        """Return the queue's configured maximum capacity."""
        return self._maxsize

    async def drain_batch(
        self,
        *,
        max_batch: int,
        max_wait_seconds: float,
    ) -> list[Any]:
        """Drain up to max_batch items, waiting for only the first item."""
        if max_batch <= 0:
            return []

        items: list[Any] = []
        try:
            first = await asyncio.wait_for(
                self._queue.get(),
                timeout=max_wait_seconds,
            )
        except TimeoutError:
            return items

        items.append(first)
        self._queue.task_done()
        while len(items) < max_batch:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            items.append(item)
            self._queue.task_done()
        return items

    async def flush(self, *, timeout: float) -> bool:  # noqa: ASYNC109
        """Wait until the queue is empty or timeout elapses."""
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except TimeoutError:
            return False
        return self._queue.qsize() == 0
