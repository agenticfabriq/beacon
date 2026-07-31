"""Logging must emit JSON to stdout with worker metadata."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from beacon_workers.logging import configure_logging, get_logger

if TYPE_CHECKING:
    import pytest


def test_logger_emits_json_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(worker_name="test-worker", log_level="INFO")
    log = get_logger().bind(tick_id="abc123")
    log.info("hello", n_items=3)

    captured = capsys.readouterr().out.strip()

    assert captured, "logger produced no output"
    payload = json.loads(captured)
    assert payload["event"] == "hello"
    assert payload["worker_name"] == "test-worker"
    assert payload["tick_id"] == "abc123"
    assert payload["n_items"] == 3
    assert payload["level"] == "info"
    assert "timestamp" in payload


def test_logger_includes_iso_timestamp(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(worker_name="t", log_level="INFO")

    get_logger().info("x")

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    ts = payload["timestamp"]
    assert ts.endswith("Z") or ts.endswith("+00:00")


def test_logger_silences_below_level(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(worker_name="t", log_level="WARNING")

    get_logger().info("should-not-appear")

    out = capsys.readouterr().out
    assert out == ""
