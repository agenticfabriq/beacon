from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from beacon_graders.graders.hierarchical_rubric import HierarchicalRubricGrader
from beacon_graders.llm.provider import JudgeCache, JudgeRequest, JudgeResponse

if TYPE_CHECKING:
    from collections.abc import Callable

    from beacon_runner.types import EvalItem, ExecutionResult


class _StubProvider:
    name = "stub"
    model_version = "stub-1"

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.last_request: JudgeRequest | None = None
        self.calls = 0

    def generate(self, request: JudgeRequest) -> JudgeResponse:
        self.calls += 1
        self.last_request = request
        return JudgeResponse(
            text=json.dumps(self._payload),
            tokens_input=42,
            tokens_output=10,
            model_version=self.model_version,
            raw={},
        )


@pytest.fixture
def good_payload() -> dict[str, Any]:
    return {
        "criteria": {
            "completeness": {"score": 0.9, "justification": "Covers all asks"},
            "correctness": {"score": 0.85, "justification": "Numbers right"},
            "clarity": {"score": 0.75, "justification": "Reads well"},
        }
    }


@pytest.fixture
def grader(good_payload: dict[str, Any]) -> tuple[HierarchicalRubricGrader, _StubProvider]:
    provider = _StubProvider(good_payload)
    cache = JudgeCache(provider=provider)
    return HierarchicalRubricGrader(judge_cache=cache), provider


def _rubric() -> dict[str, Any]:
    return {
        "criteria": [
            {"name": "completeness", "description": "..."},
            {"name": "correctness", "description": "..."},
            {"name": "clarity", "description": "..."},
        ]
    }


def test_emits_one_verdict_per_criterion(
    grader: tuple[HierarchicalRubricGrader, _StubProvider],
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    rubric_grader, _ = grader
    item = make_item(metadata={"rubric": _rubric()})
    result = make_result(output={"answer": "the report"}, output_kind="answer")
    verdicts = rubric_grader.grade(item, result)
    assert {verdict.criterion for verdict in verdicts} == {
        "completeness",
        "correctness",
        "clarity",
    }
    assert all(verdict.value is not None and 0.0 <= verdict.value <= 1.0 for verdict in verdicts)


def test_applicable_requires_rubric_in_metadata(
    grader: tuple[HierarchicalRubricGrader, _StubProvider],
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    rubric_grader, _ = grader
    no_rubric = make_item(metadata={})
    result = make_result(output={"answer": "x"}, output_kind="answer")
    assert rubric_grader.applicable(no_rubric, result) is False
    with_rubric = make_item(metadata={"rubric": {"criteria": [{"name": "x", "description": "d"}]}})
    assert rubric_grader.applicable(with_rubric, result) is True


def test_missing_criterion_in_judge_output_is_unscored_not_zero(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    """The justification said "missing"; the 0.0 beside it still counted.

    `composer.compose` builds the PASS/FAIL composite from every LLM verdict
    value, so a criterion the judge never emitted dragged the SUT's score down
    while claiming to report its own absence. None makes the item ERROR.
    """
    provider = _StubProvider({"criteria": {"completeness": {"score": 1.0, "justification": ""}}})
    rubric_grader = HierarchicalRubricGrader(judge_cache=JudgeCache(provider=provider))
    item = make_item(
        metadata={
            "rubric": {
                "criteria": [
                    {"name": "completeness", "description": "d"},
                    {"name": "correctness", "description": "d"},
                ]
            }
        }
    )
    result = make_result(output={"answer": "x"}, output_kind="answer")
    verdicts = rubric_grader.grade(item, result)
    correctness = next(verdict for verdict in verdicts if verdict.criterion == "correctness")
    assert correctness.value is None
    assert "missing" in correctness.justification.lower()


def test_clamps_score_into_unit_interval(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    provider = _StubProvider(
        {
            "criteria": {
                "completeness": {"score": 1.5, "justification": ""},
                "correctness": {"score": -0.2, "justification": ""},
            }
        }
    )
    rubric_grader = HierarchicalRubricGrader(judge_cache=JudgeCache(provider=provider))
    item = make_item(
        metadata={
            "rubric": {
                "criteria": [
                    {"name": "completeness", "description": "d"},
                    {"name": "correctness", "description": "d"},
                ]
            }
        }
    )
    result = make_result(output={"answer": "x"}, output_kind="answer")
    verdicts = rubric_grader.grade(item, result)
    by_name = {verdict.criterion: verdict.value for verdict in verdicts}
    assert by_name["completeness"] == 1.0
    assert by_name["correctness"] == 0.0


def test_cache_avoids_duplicate_calls_for_same_item(
    good_payload: dict[str, Any],
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    provider = _StubProvider(good_payload)
    rubric_grader = HierarchicalRubricGrader(judge_cache=JudgeCache(provider=provider))
    item = make_item(metadata={"rubric": _rubric()})
    result = make_result(output={"answer": "x"}, output_kind="answer")
    rubric_grader.grade(item, result)
    rubric_grader.grade(item, result)
    assert provider.last_request is not None
    assert provider.calls == 1


def test_an_unscored_criterion_does_not_count_as_a_zero(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    """The score level has the same hole the criterion level had.

    A criterion cut off mid-object carries no usable score -- no `score` key,
    or a null one -- and coerced to 0.0, which `composer.compose` counted into
    the SUT's composite exactly like a real score.
    """
    provider = _StubProvider(
        {
            "criteria": {
                "completeness": {"score": 0.9, "justification": "good"},
                "correctness": {"justification": "cut off"},
            }
        }
    )
    rubric_grader = HierarchicalRubricGrader(judge_cache=JudgeCache(provider=provider))
    item = make_item(
        metadata={
            "rubric": {
                "criteria": [
                    {"name": "completeness", "description": "d"},
                    {"name": "correctness", "description": "d"},
                ]
            }
        }
    )
    result = make_result(output={"answer": "x"}, output_kind="answer")

    verdicts = {verdict.criterion: verdict for verdict in rubric_grader.grade(item, result)}

    assert verdicts["completeness"].value == 0.9
    assert verdicts["correctness"].value is None
