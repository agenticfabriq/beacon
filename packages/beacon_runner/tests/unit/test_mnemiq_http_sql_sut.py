"""MnemiqHttpSqlSUT tests against a local stub /v1/ask server."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from beacon_runner.sut.mnemiq import MnemiqHttpSqlSUT
from beacon_runner.types import EvalItem, SolutionConfig

if TYPE_CHECKING:
    from collections.abc import Iterator


class _StubAskHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server API
        server: Any = self.server
        length = int(self.headers.get("Content-Length", "0"))
        server.requests.append(json.loads(self.rfile.read(length)))
        self.send_response(server.status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(server.response_body).encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server API
        return


class _StubServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _StubAskHandler)
        self.status_code = 200
        self.response_body: dict[str, Any] = {}
        self.requests: list[dict[str, Any]] = []

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


@pytest.fixture
def stub_server() -> Iterator[_StubServer]:
    server = _StubServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=5)


def _item() -> EvalItem:
    return EvalItem(
        item_id="bird-2",
        suite="bird_minidev_v2",
        query={"db_id": "california_schools", "question": "Top school?", "evidence": "use frpm"},
        ground_truth={"sql": "SELECT 1"},
        metadata={},
    )


def _sut(server: _StubServer, mode: str | None = None) -> MnemiqHttpSqlSUT:
    return MnemiqHttpSqlSUT(
        owner_team_id=uuid4(), base_url=server.base_url, timeout_s=5.0, mode=mode
    )


def test_answered_response_maps_sql_output(stub_server: _StubServer) -> None:
    stub_server.response_body = {
        "answer": "It is X.",
        "deferred": False,
        "sql": "SELECT name FROM schools LIMIT 1",
        "mode": "thinking",
        "cached": False,
        "timing": {"total_ms": 1234.5},
        "enrichment_version": "v9",
    }
    result = _sut(stub_server).invoke(_item(), SolutionConfig(model_id="m"))

    assert result.error is None
    assert result.output_kind == "sql"
    assert result.output["sql"] == "SELECT name FROM schools LIMIT 1"
    assert result.runtime_ms == 1234
    assert result.trace.outputs["mode"] == "thinking"


def test_deferral_is_a_structured_non_answer(stub_server: _StubServer) -> None:
    stub_server.response_body = {
        "answer": "I cannot answer this from the governed schema.",
        "deferred": True,
        "failed": False,
        "reason_code": "no_tables",
        "sql": None,
    }
    result = _sut(stub_server).invoke(_item(), SolutionConfig(model_id="m"))

    assert result.error is None
    assert result.output["sql"] == ""
    assert result.output["deferred"] is True
    assert result.output["reason"] == "no_tables"


def test_failed_answer_is_a_system_error_not_a_graded_fail(stub_server: _StubServer) -> None:
    stub_server.response_body = {
        "answer": "Could not answer: the database rejected every attempt.",
        "deferred": False,
        "failed": True,
        "reason_code": "execution_failed",
        "sql": None,
    }
    result = _sut(stub_server).invoke(_item(), SolutionConfig(model_id="m"))

    assert result.error is not None
    assert result.error.startswith("mnemiq_execution_failed")
    assert result.output["sql"] == ""
    assert result.output["failed"] is True
    assert result.output["reason"] == "execution_failed"
    assert result.trace.status == "FAILED"


def test_http_error_becomes_transport_error_result(stub_server: _StubServer) -> None:
    stub_server.status_code = 500
    stub_server.response_body = {"detail": "boom"}
    result = _sut(stub_server).invoke(_item(), SolutionConfig(model_id="m"))

    assert result.error is not None
    assert result.error.startswith("mnemiq_http_transport")
    assert result.output["sql"] == ""
    assert result.trace.status == "FAILED"


def test_request_composes_hint_and_mode(stub_server: _StubServer) -> None:
    stub_server.response_body = {"answer": "ok", "deferred": False, "sql": "SELECT 1"}
    _sut(stub_server, mode="deep").invoke(_item(), SolutionConfig(model_id="m"))

    request = stub_server.requests[0]
    assert request["question"] == "Top school?\n\nHint: use frpm"
    assert request["mode"] == "deep"


def test_validate_config_rejects_layer_overrides(stub_server: _StubServer) -> None:
    sut = _sut(stub_server)
    errors = sut.validate_config(SolutionConfig(model_id="m", layers_enabled={"verifier": False}))
    assert any("no ablation overrides" in e for e in errors)
    assert sut.layers() == []
    assert sut.identity().supported_modes == ["EVAL"]
