"""End-to-end SDK behavior against httpx.MockTransport."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
from beacon_sdk.client import BeaconClient
from beacon_sdk.errors import BeaconQueueFullError


def _success_handler() -> tuple[httpx.MockTransport, list[dict[str, Any]]]:
    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "production_trace_id": "00000000-0000-0000-0000-000000000001",
                "derived_trace_id": None,
                "created_at": "2026-06-05T00:00:00+00:00",
            },
        )

    return httpx.MockTransport(handler), posted


def _failing_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "down"})

    return httpx.MockTransport(handler)


def _capture_sdk_warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    messages: list[str] = []

    def warning(message: str, *args: object) -> None:
        messages.append(message % args if args else message)

    monkeypatch.setattr("beacon_sdk.client.log.warning", warning)
    return messages


@pytest.mark.asyncio
async def test_log_trace_posts_to_traces_endpoint() -> None:
    transport, posted = _success_handler()

    async with BeaconClient(
        base_url="https://beacon.test/",
        api_key="bcn_test_k",
        transport=transport,
        drain_interval_seconds=0.01,
    ) as client:
        ok = await client.log_trace(
            solution_id="acme",
            item_input={"q": "?"},
            item_output={"a": "!"},
            trace={"name": "r", "level": "w", "children": []},
            metadata={"latency_ms": 12},
        )
        assert ok is True
        assert await client.flush(timeout=1.0) is True

    assert len(posted) == 1
    assert posted[0]["solution_id"] == "acme"


@pytest.mark.asyncio
async def test_log_trace_returns_when_queue_full_default_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings = _capture_sdk_warnings(monkeypatch)
    hang_event = asyncio.Event()

    async def hanging_handler(request: httpx.Request) -> httpx.Response:
        await hang_event.wait()
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(hanging_handler)
    async with BeaconClient(
        base_url="https://beacon.test/",
        api_key="k",
        transport=transport,
        queue_maxsize=2,
        drain_interval_seconds=10.0,
    ) as client:
        assert (
            await client.log_trace(
                solution_id="x",
                item_input={},
                item_output={},
                trace={},
                metadata={},
            )
            is True
        )
        assert (
            await client.log_trace(
                solution_id="x",
                item_input={},
                item_output={},
                trace={},
                metadata={},
            )
            is True
        )

        start = time.monotonic()
        result = await client.log_trace(
            solution_id="x",
            item_input={},
            item_output={},
            trace={},
            metadata={},
        )
        elapsed = time.monotonic() - start
        hang_event.set()

    assert result is False
    assert elapsed < 0.1, f"log_trace blocked for {elapsed * 1000:.1f}ms"
    assert any("queue full" in message.lower() for message in warnings)


@pytest.mark.asyncio
async def test_raise_on_full_opt_in() -> None:
    async def hanging_handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(60)
        return httpx.Response(200)

    transport = httpx.MockTransport(hanging_handler)
    async with BeaconClient(
        base_url="https://beacon.test/",
        api_key="k",
        transport=transport,
        queue_maxsize=1,
        drain_interval_seconds=10.0,
        raise_on_full=True,
    ) as client:
        assert (
            await client.log_trace(
                solution_id="x",
                item_input={},
                item_output={},
                trace={},
                metadata={},
            )
            is True
        )
        with pytest.raises(BeaconQueueFullError):
            await client.log_trace(
                solution_id="x",
                item_input={},
                item_output={},
                trace={},
                metadata={},
            )


@pytest.mark.asyncio
async def test_drain_swallows_transport_errors_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings = _capture_sdk_warnings(monkeypatch)

    async with BeaconClient(
        base_url="https://beacon.test/",
        api_key="k",
        transport=_failing_transport(),
        drain_interval_seconds=0.01,
    ) as client:
        await client.log_trace(
            solution_id="x",
            item_input={},
            item_output={},
            trace={},
            metadata={},
        )
        await asyncio.sleep(0.1)
        ok = await client.log_trace(
            solution_id="x",
            item_input={},
            item_output={},
            trace={},
            metadata={},
        )

    assert ok is True
    assert any("drain" in message.lower() or "transport" in message.lower() for message in warnings)


@pytest.mark.asyncio
async def test_close_cancels_worker_and_doesnt_hang() -> None:
    transport, _posted = _success_handler()
    client = BeaconClient(
        base_url="https://beacon.test/",
        api_key="k",
        transport=transport,
        drain_interval_seconds=0.01,
    )
    await client.start()
    await client.log_trace(
        solution_id="x",
        item_input={},
        item_output={},
        trace={},
        metadata={},
    )

    start = time.monotonic()
    await client.close()

    assert time.monotonic() - start < 2.0
