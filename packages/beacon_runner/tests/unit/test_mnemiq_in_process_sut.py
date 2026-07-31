"""MnemiqInProcessSUT unit tests over an injected fake engine builder.

No mnemiq install required: the builder seam returns fake (ask, client)
pairs shaped like mnemiq's ``AgentAnswer``/``LLMClient`` surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from beacon_runner.sut.mnemiq import MnemiqInProcessSUT
from beacon_runner.sut.mnemiq.in_process import compose_question
from beacon_runner.types import EvalItem, SolutionConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

LAYER_NAMES = ("enrichment", "grounding", "verifier", "self_consistency", "mode_routing")


@dataclass
class _FakeTrace:
    target_sql: str = "SELECT 1 AS answer"
    timing: dict[str, float] = field(default_factory=lambda: {"execute_ms": 3.0, "total_ms": 9.0})
    tables_used: list[str] = field(default_factory=lambda: ["frpm"])
    enrichment_version: str = "v-test"


@dataclass
class _FakeAnswer:
    answer: str = "42"
    trace: _FakeTrace | None = None
    deferred: bool = False
    failed: bool = False
    reason_code: str | None = None
    cached: bool = False
    agreement: float | None = None
    judge_engaged: bool | None = None
    judge_override: bool | None = None
    candidates_executed: int | None = None


class _FakeClient:
    def __init__(self) -> None:
        self.total_tokens = 0
        self.calls = 0


class _Builder:
    """Records build calls and asked questions; returns a canned answer."""

    def __init__(self, answer: _FakeAnswer, tokens_per_ask: int = 17) -> None:
        self.answer = answer
        self.tokens_per_ask = tokens_per_ask
        self.build_calls: list[tuple[str, dict[str, bool]]] = []
        self.questions: list[str] = []

    def __call__(self, db_id: str, enabled: Mapping[str, bool]) -> tuple[Any, Any]:
        self.build_calls.append((db_id, dict(enabled)))
        client = _FakeClient()

        def ask(question: str) -> _FakeAnswer:
            self.questions.append(question)
            client.total_tokens += self.tokens_per_ask
            return self.answer

        return ask, client


def _sut(builder: _Builder) -> MnemiqInProcessSUT:
    return MnemiqInProcessSUT(
        owner_team_id=uuid4(),
        minidev_dir="/nonexistent/minidev",
        bird_dsn="postgresql://nobody@nowhere/none",
        enrich_cache_dir="/nonexistent/cache",
        engine_builder=builder,
    )


def _item(evidence: str = "") -> EvalItem:
    return EvalItem(
        item_id="bird-1",
        suite="bird_minidev_v2",
        query={
            "db_id": "california_schools",
            "question": "How many schools?",
            "evidence": evidence,
        },
        ground_truth={"sql": "SELECT count(*) FROM schools"},
        metadata={},
    )


def test_identity_declares_five_layers() -> None:
    sut = _sut(_Builder(_FakeAnswer(trace=_FakeTrace())))
    identity = sut.identity()
    assert identity.solution_id == "mnemiq"
    assert [layer.name for layer in identity.layers] == list(LAYER_NAMES)
    assert identity.supported_modes == ["EVAL", "NIGHTLY_LOO"]
    assert [layer.name for layer in sut.layers()] == list(LAYER_NAMES)


def test_validate_config_flags_unknown_layers_and_missing_model() -> None:
    sut = _sut(_Builder(_FakeAnswer(trace=_FakeTrace())))
    ok = sut.validate_config(SolutionConfig(model_id="m", layers_enabled={"verifier": False}))
    assert ok == []
    errors = sut.validate_config(SolutionConfig(model_id="", layers_enabled={"nope": True}))
    assert any("Unknown layers" in e for e in errors)
    assert any("model_id" in e for e in errors)


def test_invoke_answered_maps_sql_and_layer_steps() -> None:
    builder = _Builder(_FakeAnswer(trace=_FakeTrace()))
    sut = _sut(builder)
    result = sut.invoke(_item(), SolutionConfig(model_id="m"))

    assert result.output_kind == "sql"
    assert result.output["sql"] == "SELECT 1 AS answer"
    assert result.error is None
    assert result.tokens_output == 17
    levels = {child.level: child.status for child in result.trace.children}
    assert levels == {f"layer:{name}": "COMPLETED" for name in LAYER_NAMES}
    assert result.trace.outputs["enrichment_version"] == "v-test"


def test_invoke_disabled_layer_marks_step_skipped_and_reaches_builder() -> None:
    builder = _Builder(_FakeAnswer(trace=_FakeTrace()))
    sut = _sut(builder)
    config = SolutionConfig(model_id="m", layers_enabled={"self_consistency": False})
    result = sut.invoke(_item(), config)

    (_, enabled) = builder.build_calls[0]
    assert enabled["self_consistency"] is False
    step = next(c for c in result.trace.children if c.level == "layer:self_consistency")
    assert step.status == "SKIPPED"


def test_invoke_deferral_is_a_non_answer_not_an_error() -> None:
    answer = _FakeAnswer(
        answer="I do not have access to that table.",
        deferred=True,
        reason_code="authorization",
        trace=None,
    )
    sut = _sut(_Builder(answer))
    result = sut.invoke(_item(), SolutionConfig(model_id="m"))

    assert result.error is None
    assert result.output["sql"] == ""
    assert result.output["deferred"] is True
    assert result.output["reason"] == "authorization"
    assert result.trace.status == "COMPLETED"


def test_invoke_failed_surfaces_system_error() -> None:
    answer = _FakeAnswer(
        answer="Could not answer: the database rejected every attempt.",
        failed=True,
        reason_code="execution_failed",
        trace=None,
    )
    sut = _sut(_Builder(answer))
    result = sut.invoke(_item(), SolutionConfig(model_id="m"))

    assert result.error is not None
    assert result.error.startswith("mnemiq_execution_failed")
    assert result.output["sql"] == ""
    assert result.trace.status == "FAILED"


def test_engines_are_cached_per_db_and_arm() -> None:
    builder = _Builder(_FakeAnswer(trace=_FakeTrace()))
    sut = _sut(builder)
    config = SolutionConfig(model_id="m")

    sut.invoke(_item(), config)
    sut.invoke(_item(), config)
    assert len(builder.build_calls) == 1

    sut.invoke(_item(), SolutionConfig(model_id="m", layers_enabled={"verifier": False}))
    assert len(builder.build_calls) == 2


def test_question_composition_appends_evidence_hint() -> None:
    builder = _Builder(_FakeAnswer(trace=_FakeTrace()))
    sut = _sut(builder)
    sut.invoke(_item(evidence="rate = a / b"), SolutionConfig(model_id="m"))

    assert builder.questions == ["How many schools?\n\nHint: rate = a / b"]
    assert compose_question({"question": "Q", "evidence": ""}) == "Q"
