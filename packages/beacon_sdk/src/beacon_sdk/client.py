"""Async Beacon SDK client."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Self, cast

import httpx

from beacon_sdk.errors import BeaconQueueFullError, BeaconValidationError
from beacon_sdk.queue import BackpressureQueue

if TYPE_CHECKING:
    from types import TracebackType
    from uuid import UUID

log = logging.getLogger("beacon_sdk")

_DEFAULT_QUEUE_MAXSIZE = 1000
_DEFAULT_BATCH_SIZE = 20
_DEFAULT_DRAIN_INTERVAL_SECONDS = 0.5
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
_DEFAULT_CLOSE_FLUSH_TIMEOUT_SECONDS = 1.0


class BeaconClient:
    """Async, best-effort production-trace client."""

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
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.batch_size = batch_size
        self.drain_interval_seconds = drain_interval_seconds
        self.raise_on_full = raise_on_full

        self._queue = BackpressureQueue(maxsize=queue_maxsize)
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(request_timeout_seconds),
            headers=self._default_headers(api_key),
        )
        self._worker_task: asyncio.Task[None] | None = None
        self._pending_posts = 0
        self._idle_event = asyncio.Event()
        self._idle_event.set()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        """Start the background drain task that POSTs queued traces."""
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(
                self._worker_loop(),
                name="beacon-sdk-drain",
            )

    async def close(self) -> None:
        """Flush, cancel the drain task, and close the underlying HTTP client."""
        await self.flush(timeout=_DEFAULT_CLOSE_FLUSH_TIMEOUT_SECONDS)
        if self._worker_task is not None:
            self._worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None
        await self._http.aclose()

    async def flush(self, *, timeout: float = 5.0) -> bool:  # noqa: ASYNC109
        """Wait until queued and in-flight traces are fully handled."""
        if self._queue.size() == 0 and self._pending_posts == 0:
            return True
        try:
            await asyncio.wait_for(self._idle_event.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return self._queue.size() == 0 and self._pending_posts == 0

    async def log_trace(
        self,
        *,
        solution_id: str,
        item_input: dict[str, Any],
        item_output: dict[str, Any],
        trace: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        project_id: UUID | None = None,
        is_eval_candidate: bool = False,
    ) -> bool:
        """Enqueue a trace for async POST to /v1/traces."""
        if self._worker_task is None:
            await self.start()

        try:
            payload = self._build_payload(
                solution_id=solution_id,
                project_id=project_id,
                item_input=item_input,
                item_output=item_output,
                trace=trace,
                metadata=metadata or {},
                is_eval_candidate=is_eval_candidate,
            )
        except ValueError as exc:
            raise BeaconValidationError(str(exc)) from exc

        accepted = self._queue.try_put(payload)
        if not accepted:
            log.warning(
                "beacon_sdk: queue full; dropping trace "
                "(queue_size=%d, maxsize=%d, solution_id=%s)",
                self._queue.size(),
                self._queue.maxsize(),
                solution_id,
            )
            if self.raise_on_full:
                raise BeaconQueueFullError(
                    f"beacon_sdk queue full (maxsize={self._queue.maxsize()})"
                )
            return False

        self._idle_event.clear()
        return True

    async def _worker_loop(self) -> None:
        while True:
            await asyncio.sleep(self.drain_interval_seconds)
            try:
                batch = await self._queue.drain_batch(
                    max_batch=self.batch_size,
                    max_wait_seconds=0.001,
                )
                if not batch:
                    self._set_idle_if_done()
                    continue
                payloads = cast("list[dict[str, Any]]", batch)
                self._pending_posts += len(payloads)
                try:
                    await self._post_batch(payloads)
                finally:
                    self._pending_posts -= len(payloads)
                    self._set_idle_if_done()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("beacon_sdk: drain loop iteration failed: %s (continuing)", exc)
                self._set_idle_if_done()

    async def _post_batch(self, batch: list[dict[str, Any]]) -> None:
        for item in batch:
            try:
                response = await self._http.post(
                    f"{self.base_url}/v1/traces",
                    json=item,
                )
            except httpx.HTTPError as exc:
                log.warning("beacon_sdk: transport error: %s; dropping", exc)
                continue

            if response.status_code >= 500:
                log.warning(
                    "beacon_sdk: transport %s for /v1/traces; dropping",
                    response.status_code,
                )
            elif response.status_code in (401, 403):
                log.warning("beacon_sdk: auth failure %s; check API key", response.status_code)
            elif response.status_code >= 400:
                log.warning(
                    "beacon_sdk: client error %s: %s",
                    response.status_code,
                    response.text[:200],
                )

    def _set_idle_if_done(self) -> None:
        if self._queue.size() == 0 and self._pending_posts == 0:
            self._idle_event.set()

    @staticmethod
    def _default_headers(api_key: str | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key
        return headers

    @staticmethod
    def _build_payload(
        *,
        solution_id: str,
        project_id: UUID | None,
        item_input: dict[str, Any],
        item_output: dict[str, Any],
        trace: dict[str, Any],
        metadata: dict[str, Any],
        is_eval_candidate: bool,
    ) -> dict[str, Any]:
        if not solution_id:
            raise ValueError("solution_id must be non-empty")
        return {
            "solution_id": solution_id,
            "project_id": str(project_id) if project_id is not None else None,
            "item_input": item_input,
            "item_output": item_output,
            "trace": trace,
            "metadata": metadata,
            "is_eval_candidate": is_eval_candidate,
        }
