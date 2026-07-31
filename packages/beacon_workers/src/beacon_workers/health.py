"""Tiny stdlib HTTP health endpoint for worker processes."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast


@dataclass
class HealthState:
    """Tracks the last successful worker tick."""

    expected_interval_seconds: float
    _last_success_monotonic: float | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def mark_success(self) -> None:
        """Record that the worker completed a tick successfully."""
        with self._lock:
            self._last_success_monotonic = time.monotonic()

    def is_healthy(self) -> tuple[bool, str]:
        """Return health status and a short diagnostic message."""
        with self._lock:
            last_success = self._last_success_monotonic

        if last_success is None:
            return False, "no successful tick yet"

        age_seconds = time.monotonic() - last_success
        budget_seconds = 2.0 * self.expected_interval_seconds
        if age_seconds > budget_seconds:
            return (
                False,
                f"stale: last success {age_seconds:.1f}s ago (budget {budget_seconds:.1f}s)",
            )
        return True, f"ok: last success {age_seconds:.1f}s ago"


class _HealthServer(ThreadingHTTPServer):
    state: HealthState


class _HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        """Suppress the default stderr access log."""
        return

    def do_GET(self) -> None:  # noqa: N802
        """Serve the worker health endpoint."""
        if self.path != "/healthz":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return

        server = cast("_HealthServer", self.server)
        is_healthy, message = server.state.is_healthy()
        self.send_response(200 if is_healthy else 500)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(message.encode("utf-8"))


def start_health_server(*, port: int, state: HealthState) -> _HealthServer:
    """Start a background health server and return the server instance."""
    server = _HealthServer(("0.0.0.0", port), _HealthHandler)  # noqa: S104
    server.state = state
    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name="health-server",
    )
    thread.start()
    return server
