"""HTTP smoke-arm SUT for a running ``mnemiq serve --http`` product surface.

A thin stdlib-only client for ``POST /v1/ask``. The product API is
configuration-free by design: this SUT runs whatever the server is configured
to run and declares no ablatable layers -- layer sweeps belong to
``MnemiqInProcessSUT``. Engine deferrals arrive as 200 responses with
``deferred: true`` (refusal is an answer, not a transport error) and map to a
non-answer result beacon grades as not-passing.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from beacon_runner.sut.mnemiq.in_process import compose_question
from beacon_runner.types import (
    EvalItem,
    ExecutionResult,
    ExecutionStep,
    Layer,
    SolutionConfig,
    SolutionIdentity,
)

if TYPE_CHECKING:
    from uuid import UUID


class MnemiqHttpSqlSUT:
    """Client for ``POST /v1/ask`` on a running mnemiq HTTP server."""

    SOLUTION_ID = "mnemiq-http"
    VERSION = "0.1.0.dev0"
    SUMMARY = (
        "Smoke-arm client for the mnemiq HTTP product surface (POST /v1/ask); "
        "fixed server-side configuration, no ablation."
    )

    def __init__(
        self,
        *,
        owner_team_id: UUID,
        base_url: str,
        timeout_s: float = 300.0,
        mode: str | None = None,
    ) -> None:
        self._owner_team_id = owner_team_id
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._mode = mode

    def identity(self) -> SolutionIdentity:
        return SolutionIdentity(
            solution_id=self.SOLUTION_ID,
            version=self.VERSION,
            owner_team=self._owner_team_id,
            summary=self.SUMMARY,
            supported_modes=["EVAL"],
            layers=[],
        )

    def layers(self) -> list[Layer]:
        return []

    def validate_config(self, config: SolutionConfig) -> list[str]:
        errors: list[str] = []
        if config.layers_enabled:
            errors.append(
                "the mnemiq HTTP surface accepts no ablation overrides; "
                "use MnemiqInProcessSUT for layer sweeps"
            )
        if not config.model_id:
            errors.append("model_id is required")
        return errors

    def invoke(self, item: EvalItem, config: SolutionConfig) -> ExecutionResult:
        question = compose_question(item.query)
        payload: dict[str, Any] = {"question": question}
        if self._mode is not None:
            payload["mode"] = self._mode

        started = time.monotonic()
        try:
            body = self._post_ask(payload)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            runtime_ms = int((time.monotonic() - started) * 1000)
            return self._transport_error_result(item, error=str(exc), runtime_ms=runtime_ms)
        runtime_ms = int((time.monotonic() - started) * 1000)

        deferred = bool(body.get("deferred", False))
        failed = bool(body.get("failed", False))
        reason = body.get("reason_code")
        sql = str(body.get("sql") or "")
        timing = body.get("timing") or {}
        if isinstance(timing, dict) and "total_ms" in timing:
            runtime_ms = int(float(timing["total_ms"]))

        root = ExecutionStep(
            uuid=f"root-{item.item_id}",
            name="mnemiq_http_ask",
            level="workflow",
            status="FAILED" if failed else "COMPLETED",
            outputs={
                "output_kind": "sql",
                "deferred": deferred,
                "failed": failed,
                "reason": reason,
                "mode": body.get("mode"),
                "cached": body.get("cached"),
                "agreement": body.get("agreement"),
                "judge_engaged": body.get("judge_engaged"),
                "judge_override": body.get("judge_override"),
                "candidates_executed": body.get("candidates_executed"),
                "enrichment_version": body.get("enrichment_version"),
                "tables_used": body.get("tables_used"),
            },
        )
        answer_text = str(body.get("answer", ""))
        if failed:
            # Source outage, not an abstention (mnemiq M6/M14): a system error,
            # so beacon composes ERROR rather than a graded FAIL.
            output: dict[str, Any] = {
                "sql": "",
                "answer": answer_text,
                "failed": True,
                "reason": reason,
            }
            error: str | None = f"mnemiq_execution_failed: {answer_text[:200]}"
        elif deferred:
            output = {"sql": "", "answer": answer_text, "deferred": True, "reason": reason}
            error = None
        else:
            output = {"sql": sql, "item_id": item.item_id}
            error = None
        return ExecutionResult(
            output=output,
            output_kind="sql",
            trace=root,
            runtime_ms=runtime_ms,
            error=error,
        )

    def _post_ask(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(  # noqa: S310 - caller-configured http(s) endpoint
            f"{self._base_url}/v1/ask",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout_s) as response:  # noqa: S310 - caller-configured http(s) endpoint
            raw = response.read().decode("utf-8")
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError(f"unexpected /v1/ask response shape: {type(body).__name__}")
        return body

    def _transport_error_result(
        self, item: EvalItem, *, error: str, runtime_ms: int
    ) -> ExecutionResult:
        root = ExecutionStep(
            uuid=f"root-{item.item_id}",
            name="mnemiq_http_ask",
            level="workflow",
            status="FAILED",
            error=error,
        )
        return ExecutionResult(
            output={"sql": ""},
            output_kind="sql",
            trace=root,
            runtime_ms=runtime_ms,
            error=f"mnemiq_http_transport: {error[:300]}",
        )
