"""Sync facade over the async Beacon SDK client."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import TYPE_CHECKING, Any, Self

from beacon_sdk.client import BeaconClient
from beacon_sdk.errors import BeaconQueueFullError

if TYPE_CHECKING:
    from types import TracebackType
    from uuid import UUID

    import httpx

_DEFAULT_QUEUE_MAXSIZE = 1000
_DEFAULT_BATCH_SIZE = 20
_DEFAULT_DRAIN_INTERVAL_SECONDS = 0.5
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
_DEFAULT_START_TIMEOUT_SECONDS = 10.0
_DEFAULT_CLOSE_TIMEOUT_SECONDS = 10.0
_DEFAULT_THREAD_JOIN_SECONDS = 5.0


class BeaconSyncClient:
    """Sync, best-effort facade for callers that cannot use async directly."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
        queue_maxsize: int = _DEFAULT_QUEUE_MAXSIZE,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        drain_interval_seconds: float = _DEFAULT_DRAIN_INTERVAL_SECONDS,
        request_timeout_seconds: float = _DEFAULT_REQUEST_TIMEOUT_SECONDS,
        raise_on_full: bool = False,
    ) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            name="beacon-sdk-loop",
            daemon=True,
        )
        self._thread.start()
        self._closed = False

        future = asyncio.run_coroutine_threadsafe(
            self._make_async_client(
                base_url=base_url,
                api_key=api_key,
                transport=transport,
                queue_maxsize=queue_maxsize,
                batch_size=batch_size,
                drain_interval_seconds=drain_interval_seconds,
                request_timeout_seconds=request_timeout_seconds,
                raise_on_full=raise_on_full,
            ),
            self._loop,
        )
        self._client = future.result(timeout=_DEFAULT_START_TIMEOUT_SECONDS)

    async def _make_async_client(self, **kwargs: Any) -> BeaconClient:
        client = BeaconClient(**kwargs)
        await client.start()
        return client

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def log_trace_sync(
        self,
        *,
        solution_id: str,
        item_input: dict[str, Any],
        item_output: dict[str, Any],
        trace: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        project_id: UUID | None = None,
        is_eval_candidate: bool = False,
        wait_timeout: float = 2.0,
    ) -> bool:
        """Return True when enqueued, False when dropped or timed out."""
        if self._closed:
            return False

        future = asyncio.run_coroutine_threadsafe(
            self._client.log_trace(
                solution_id=solution_id,
                project_id=project_id,
                item_input=item_input,
                item_output=item_output,
                trace=trace,
                metadata=metadata,
                is_eval_candidate=is_eval_candidate,
            ),
            self._loop,
        )
        try:
            return future.result(timeout=wait_timeout)
        except BeaconQueueFullError:
            return False
        except FutureTimeoutError:
            future.cancel()
            return False

    def flush(self, *, timeout: float = 5.0) -> bool:
        """Block until queued and in-flight traces are drained or timeout elapses."""
        if self._closed:
            return True

        future = asyncio.run_coroutine_threadsafe(
            self._client.flush(timeout=timeout),
            self._loop,
        )
        try:
            return future.result(timeout=timeout + 1.0)
        except FutureTimeoutError:
            future.cancel()
            return False

    def close(self) -> None:
        """Close the underlying async client and stop the background event loop."""
        if self._closed:
            return
        self._closed = True

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._client.close(),
                self._loop,
            )
            future.result(timeout=_DEFAULT_CLOSE_TIMEOUT_SECONDS)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=_DEFAULT_THREAD_JOIN_SECONDS)
            self._loop.close()
