from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from beacon_graders.graders.dabstep_answer_matcher import DabstepAnswerMatcher

if TYPE_CHECKING:
    from collections.abc import Callable

    from beacon_graders.types import Verdict
    from beacon_runner.types import EvalItem, ExecutionResult


@pytest.fixture
def grader() -> DabstepAnswerMatcher:
    return DabstepAnswerMatcher()


def _grade(
    grader: DabstepAnswerMatcher,
    candidate: Any,
    gold: Any,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> Verdict:
    item = make_item(ground_truth={"answer": gold})
    result = make_result(output={"answer": candidate}, output_kind="answer")
    verdicts = grader.grade(item, result)
    return verdicts[0]


def test_exact_numeric_match(
    grader: DabstepAnswerMatcher,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    verdict = _grade(grader, "42", "42", make_item, make_result)
    assert verdict.bool_value is True


def test_numeric_with_tolerance(
    grader: DabstepAnswerMatcher,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    verdict = _grade(grader, "0.99999", "1.0", make_item, make_result)
    assert verdict.bool_value is True


def test_numeric_mismatch_outside_tolerance(
    grader: DabstepAnswerMatcher,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    verdict = _grade(grader, "0.9", "1.0", make_item, make_result)
    assert verdict.bool_value is False


def test_currency(
    grader: DabstepAnswerMatcher,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    verdict = _grade(grader, "$1,234.56", "1234.56", make_item, make_result)
    assert verdict.bool_value is True


def test_percentage_normalized(
    grader: DabstepAnswerMatcher,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    verdict = _grade(grader, "12.34", "12.34", make_item, make_result)
    assert verdict.bool_value is True


def test_comma_separated_number(
    grader: DabstepAnswerMatcher,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    verdict = _grade(grader, "1,000,000", "1000000", make_item, make_result)
    assert verdict.bool_value is True


def test_list_set_equality(
    grader: DabstepAnswerMatcher,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    verdict = _grade(
        grader,
        "apple, banana, cherry",
        "cherry; banana; apple",
        make_item,
        make_result,
    )
    assert verdict.bool_value is True


def test_empty_list_vs_not_applicable(
    grader: DabstepAnswerMatcher,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    verdict = _grade(grader, "Not Applicable", "[]", make_item, make_result)
    assert verdict.bool_value is True


def test_fuzzy_string_match_passes(
    grader: DabstepAnswerMatcher,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    verdict = _grade(
        grader,
        "United States of America",
        "United States of Americaa",
        make_item,
        make_result,
    )
    assert verdict.bool_value is True


def test_fuzzy_string_mismatch(
    grader: DabstepAnswerMatcher,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    verdict = _grade(grader, "United States", "Canada", make_item, make_result)
    assert verdict.bool_value is False


def test_missing_candidate_fails(
    grader: DabstepAnswerMatcher,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"answer": "42"})
    result = make_result(output={}, output_kind="answer")
    verdict = grader.grade(item, result)[0]
    assert verdict.bool_value is False


def test_applicable_only_when_output_kind_is_answer(
    grader: DabstepAnswerMatcher,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    answer_item = make_item(ground_truth={"answer": "x"})
    answer_result = make_result(output={"answer": "x"}, output_kind="answer")
    assert grader.applicable(answer_item, answer_result) is True
    sql_result = make_result(output={"sql": "select 1"}, output_kind="sql")
    assert grader.applicable(answer_item, sql_result) is False


def test_verdict_has_grader_and_version(
    grader: DabstepAnswerMatcher,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    verdict = _grade(grader, "x", "x", make_item, make_result)
    assert verdict.grader == "dabstep_answer_matcher"
    assert verdict.grader_version.startswith("v")
