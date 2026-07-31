"""BackpressureQueue: bounded, non-blocking try_put, drop-on-full semantics."""

from __future__ import annotations

import asyncio
import time

import pytest
from beacon_sdk.queue import BackpressureQueue


@pytest.mark.asyncio
async def test_try_put_succeeds_when_empty() -> None:
    queue = BackpressureQueue(maxsize=10)

    assert queue.try_put("a") is True
    assert queue.size() == 1


@pytest.mark.asyncio
async def test_try_put_fills_to_capacity() -> None:
    queue = BackpressureQueue(maxsize=3)

    assert queue.try_put("a") is True
    assert queue.try_put("b") is True
    assert queue.try_put("c") is True
    assert queue.size() == 3


@pytest.mark.asyncio
async def test_try_put_returns_false_when_full() -> None:
    """Critical: try_put on a full queue must return without blocking."""
    queue = BackpressureQueue(maxsize=2)

    assert queue.try_put("a") is True
    assert queue.try_put("b") is True
    assert queue.try_put("c") is False
    assert queue.size() == 2


@pytest.mark.asyncio
async def test_try_put_is_non_blocking_under_full_load() -> None:
    queue = BackpressureQueue(maxsize=2)
    start = time.monotonic()
    accepted = 0

    for index in range(10_000):
        if queue.try_put(index):
            accepted += 1

    elapsed = time.monotonic() - start
    assert elapsed < 0.5, f"try_put loop took {elapsed * 1000:.1f}ms"
    assert accepted == 2


@pytest.mark.asyncio
async def test_drain_yields_items_in_order() -> None:
    queue = BackpressureQueue(maxsize=10)
    queue.try_put("a")
    queue.try_put("b")
    queue.try_put("c")

    items = await queue.drain_batch(max_batch=10, max_wait_seconds=0.01)

    assert items == ["a", "b", "c"]
    assert queue.size() == 0


@pytest.mark.asyncio
async def test_drain_with_empty_queue_returns_empty_after_timeout() -> None:
    queue = BackpressureQueue(maxsize=10)
    start = time.monotonic()

    items = await queue.drain_batch(max_batch=5, max_wait_seconds=0.02)
    elapsed = time.monotonic() - start

    assert items == []
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_drain_respects_max_batch() -> None:
    queue = BackpressureQueue(maxsize=100)
    for index in range(20):
        queue.try_put(index)

    items = await queue.drain_batch(max_batch=5, max_wait_seconds=0.01)

    assert len(items) == 5
    assert queue.size() == 15


@pytest.mark.asyncio
async def test_flush_waits_until_drained() -> None:
    queue = BackpressureQueue(maxsize=10)
    queue.try_put("a")
    queue.try_put("b")

    async def consumer() -> None:
        await asyncio.sleep(0.01)
        await queue.drain_batch(max_batch=10, max_wait_seconds=0.05)

    consumer_task = asyncio.create_task(consumer())
    ok = await queue.flush(timeout=0.5)

    assert ok is True
    assert queue.size() == 0
    await consumer_task


@pytest.mark.asyncio
async def test_flush_times_out_when_not_drained() -> None:
    queue = BackpressureQueue(maxsize=10)
    queue.try_put("a")

    ok = await queue.flush(timeout=0.05)

    assert ok is False
    assert queue.size() == 1
