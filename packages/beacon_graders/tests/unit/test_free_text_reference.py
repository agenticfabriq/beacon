from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from beacon_graders.graders.free_text_reference import FreeTextReferenceGrader
from beacon_graders.llm.provider import JudgeCache, JudgeRequest, JudgeResponse

if TYPE_CHECKING:
    from collections.abc import Callable

    from beacon_runner.types import EvalItem, ExecutionResult


class _StubProvider:
    name = "stub"
    model_version = "stub-1"

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def generate(self, request: JudgeRequest) -> JudgeResponse:  # noqa: ARG002
        return JudgeResponse(
            text=json.dumps(self._payload),
            tokens_input=10,
            tokens_output=5,
            model_version=self.model_version,
            raw={},
        )


def _grader_with(payload: dict[str, Any]) -> FreeTextReferenceGrader:
    return FreeTextReferenceGrader(judge_cache=JudgeCache(provider=_StubProvider(payload)))


def test_emits_recall_and_citation_verdicts(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    grader = _grader_with(
        {
            "insight_recall": {"score": 0.8, "justification": "4 of 5"},
            "citation_correctness": {"score": 1.0, "justification": "all good"},
        }
    )
    item = make_item(
        query={"question": "Summarize"},
        ground_truth={
            "reference_insights": ["i1", "i2", "i3", "i4", "i5"],
            "data_sources": ["table_a", "table_b"],
        },
        metadata={"output_format": "narrative"},
    )
    result = make_result(
        output={"narrative": "lorem", "citations": ["table_a"]},
        output_kind="narrative",
    )
    verdicts = grader.grade(item, result)
    by_name = {verdict.criterion: verdict.value for verdict in verdicts}
    assert by_name == {"insight_recall": 0.8, "citation_correctness": 1.0}


def test_applicable_only_when_output_kind_is_narrative(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    grader = _grader_with(
        {
            "insight_recall": {"score": 0.0, "justification": ""},
            "citation_correctness": {"score": 0.0, "justification": ""},
        }
    )
    item = make_item(ground_truth={"reference_insights": ["x"], "data_sources": []})
    narrative_result = make_result(output={"narrative": "x"}, output_kind="narrative")
    sql_result = make_result(output={"sql": "x"}, output_kind="sql")
    assert grader.applicable(item, narrative_result) is True
    assert grader.applicable(item, sql_result) is False


def test_applicable_requires_reference_insights(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    grader = _grader_with(
        {
            "insight_recall": {"score": 0.0, "justification": ""},
            "citation_correctness": {"score": 0.0, "justification": ""},
        }
    )
    item_no_ref = make_item(ground_truth={"data_sources": []})
    result = make_result(output={"narrative": "x"}, output_kind="narrative")
    assert grader.applicable(item_no_ref, result) is False


def test_judge_failure_yields_zero_verdicts(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    class _ErrorProvider:
        name = "err"
        model_version = "v"

        def generate(self, request: JudgeRequest) -> JudgeResponse:  # noqa: ARG002
            raise RuntimeError("boom")

    grader = FreeTextReferenceGrader(judge_cache=JudgeCache(provider=_ErrorProvider()))
    item = make_item(ground_truth={"reference_insights": ["x"], "data_sources": []})
    result = make_result(output={"narrative": "x"}, output_kind="narrative")
    verdicts = grader.grade(item, result)
    assert all(verdict.value == 0.0 for verdict in verdicts)
    assert any("boom" in verdict.justification for verdict in verdicts)
