from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from beacon_graders.graders.text2vis_data_grounded import Text2VisDataGroundedGrader

if TYPE_CHECKING:
    from collections.abc import Callable

    from beacon_runner.types import EvalItem, ExecutionResult


@pytest.fixture
def grader() -> Text2VisDataGroundedGrader:
    return Text2VisDataGroundedGrader()


def _gold_table() -> list[dict[str, Any]]:
    return [
        {"year": 2024, "rev": 100},
        {"year": 2025, "rev": 120},
    ]


def test_direct_table_match_passes(
    grader: Text2VisDataGroundedGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"data_table": _gold_table()})
    result = make_result(
        output={
            "data_table": [
                {"year": 2025, "rev": 120},
                {"year": 2024, "rev": 100},
            ]
        },
        output_kind="chart",
    )
    verdict = grader.grade(item, result)[0]
    assert verdict.bool_value is True


def test_table_mismatch_fails(
    grader: Text2VisDataGroundedGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"data_table": _gold_table()})
    result = make_result(
        output={"data_table": [{"year": 2024, "rev": 100}]},
        output_kind="chart",
    )
    verdict = grader.grade(item, result)[0]
    assert verdict.bool_value is False


def test_executes_data_code_when_no_table_provided(
    grader: Text2VisDataGroundedGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"data_table": _gold_table()})
    code = "result = [\n    {'year': 2024, 'rev': 100},\n    {'year': 2025, 'rev': 120},\n]\n"
    result = make_result(output={"data_code": code}, output_kind="chart")
    verdict = grader.grade(item, result)[0]
    assert verdict.bool_value is True


def test_sandbox_rejects_os_import(
    grader: Text2VisDataGroundedGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"data_table": []})
    code = "import os\nresult = []\n"
    result = make_result(output={"data_code": code}, output_kind="chart")
    verdict = grader.grade(item, result)[0]
    assert verdict.bool_value is False
    assert "import" in verdict.justification.lower() or "denied" in verdict.justification.lower()


def test_runtime_exception_reported_in_verdict(
    grader: Text2VisDataGroundedGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"data_table": []})
    code = "result = 1 / 0\n"
    result = make_result(output={"data_code": code}, output_kind="chart")
    verdict = grader.grade(item, result)[0]
    assert verdict.bool_value is False
    assert "ZeroDivisionError" in verdict.justification


def test_applicable_only_for_chart_outputs(
    grader: Text2VisDataGroundedGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"data_table": []})
    chart_result = make_result(output={"data_table": []}, output_kind="chart")
    answer_result = make_result(output={"answer": "x"}, output_kind="answer")
    assert grader.applicable(item, chart_result) is True
    assert grader.applicable(item, answer_result) is False


def test_normalises_pandas_like_dict_output(
    grader: Text2VisDataGroundedGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"data_table": _gold_table()})
    result = make_result(
        output={"data_table": {"year": [2024, 2025], "rev": [100, 120]}},
        output_kind="chart",
    )
    verdict = grader.grade(item, result)[0]
    assert verdict.bool_value is True
