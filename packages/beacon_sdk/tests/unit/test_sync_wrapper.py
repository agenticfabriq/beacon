"""BeaconSyncClient: sync facade over the async client."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

import httpx
from beacon_sdk.sync_wrapper import BeaconSyncClient

if TYPE_CHECKING:
    import pytest


def _capture_handler() -> tuple[httpx.MockTransport, list[dict[str, Any]]]:
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


def _capture_sdk_warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    messages: list[str] = []

    def warning(message: str, *args: object) -> None:
        messages.append(message % args if args else message)

    monkeypatch.setattr("beacon_sdk.client.log.warning", warning)
    return messages


def test_sync_client_logs_trace_and_flushes() -> None:
    transport, posted = _capture_handler()
    client = BeaconSyncClient(
        base_url="https://beacon.test/",
        api_key="bcn_test",
        transport=transport,
        drain_interval_seconds=0.01,
    )
    try:
        ok = client.log_trace_sync(
            solution_id="acme",
            item_input={"q": "?"},
            item_output={"a": "!"},
            trace={"name": "r", "level": "w", "children": []},
            metadata={"latency_ms": 10},
        )
        assert ok is True
        assert client.flush(timeout=2.0) is True
    finally:
        client.close()

    assert len(posted) == 1
    assert posted[0]["solution_id"] == "acme"


def test_sync_log_trace_does_not_block_when_queue_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings = _capture_sdk_warnings(monkeypatch)

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(60)
        return httpx.Response(200)

    transport = httpx.MockTransport(hang)
    client = BeaconSyncClient(
        base_url="https://beacon.test/",
        api_key="k",
        transport=transport,
        queue_maxsize=1,
        drain_interval_seconds=10.0,
    )
    try:
        assert (
            client.log_trace_sync(
                solution_id="x",
                item_input={},
                item_output={},
                trace={},
                metadata={},
            )
            is True
        )
        start = time.monotonic()
        result = client.log_trace_sync(
            solution_id="x",
            item_input={},
            item_output={},
            trace={},
            metadata={},
        )
        elapsed = time.monotonic() - start
    finally:
        client.close()

    assert result is False
    assert elapsed < 0.5, f"log_trace_sync blocked for {elapsed * 1000:.1f}ms"
    assert any("queue full" in message.lower() for message in warnings)


def test_sync_context_manager_flushes_on_exit() -> None:
    transport, posted = _capture_handler()

    with BeaconSyncClient(
        base_url="https://beacon.test/",
        api_key="k",
        transport=transport,
        drain_interval_seconds=0.01,
    ) as client:
        assert (
            client.log_trace_sync(
                solution_id="x",
                item_input={},
                item_output={},
                trace={},
                metadata={},
            )
            is True
        )

    assert len(posted) == 1
