"""Health endpoint status behavior."""

from __future__ import annotations

import time
import urllib.error
import urllib.request

from beacon_workers.health import HealthState, start_health_server


def _get(port: int, path: str = "/healthz") -> tuple[int, str]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:  # noqa: S310
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_health_200_on_fresh_tick(unused_tcp_port: int) -> None:
    state = HealthState(expected_interval_seconds=10)
    state.mark_success()
    server = start_health_server(port=unused_tcp_port, state=state)
    try:
        status, body = _get(unused_tcp_port)

        assert status == 200
        assert "ok" in body.lower()
    finally:
        server.shutdown()


def test_health_500_on_stale_tick(unused_tcp_port: int) -> None:
    state = HealthState(expected_interval_seconds=1)
    state.mark_success()
    time.sleep(2.5)
    server = start_health_server(port=unused_tcp_port, state=state)
    try:
        status, body = _get(unused_tcp_port)

        assert status == 500
        assert "stale" in body.lower()
    finally:
        server.shutdown()


def test_health_500_before_first_tick(unused_tcp_port: int) -> None:
    state = HealthState(expected_interval_seconds=10)
    server = start_health_server(port=unused_tcp_port, state=state)
    try:
        status, _body = _get(unused_tcp_port)

        assert status == 500
    finally:
        server.shutdown()


def test_unknown_path_404(unused_tcp_port: int) -> None:
    state = HealthState(expected_interval_seconds=10)
    state.mark_success()
    server = start_health_server(port=unused_tcp_port, state=state)
    try:
        status, _body = _get(unused_tcp_port, "/nope")

        assert status == 404
    finally:
        server.shutdown()
