"""The answer-authority grader: pushed rows against materialized gold."""

from __future__ import annotations

from typing import Any

from beacon_graders.graders.result_set_match import ResultSetMatchGrader
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep


def _item(gold: dict[str, Any]) -> EvalItem:
    return EvalItem(
        item_id="i-1",
        suite="s",
        query={"question": "q?"},
        ground_truth=gold,
        metadata={},
    )


def _result(output: dict[str, Any]) -> ExecutionResult:
    return ExecutionResult(
        output=output,
        output_kind="sql",
        trace=ExecutionStep(uuid="root", name="root", level="workflow"),
        tokens_input=1,
        tokens_output=1,
        runtime_ms=1,
    )


def _gold(rows: list[list[Any]], **over: Any) -> dict[str, Any]:
    gold: dict[str, Any] = {
        "sql": "SELECT a FROM t",
        "columns": ["a"],
        "rows": rows,
        "row_count": len(rows),
    }
    gold.update(over)
    return gold


def _push(rows: list[Any], **over: Any) -> dict[str, Any]:
    output: dict[str, Any] = {"sql": "SELECT a FROM candidate", "rows": rows}
    output.update(over)
    return output


def _grade(gold: dict[str, Any], output: dict[str, Any]) -> tuple[Any, Any]:
    verdicts = ResultSetMatchGrader().grade(_item(gold), _result(output))
    exact = next(v for v in verdicts if v.metric != "got_facts")
    facts = next(v for v in verdicts if v.metric == "got_facts")
    return exact, facts


def test_not_applicable_without_pushed_rows() -> None:
    grader = ResultSetMatchGrader()
    assert not grader.applicable(_item(_gold([[1]])), _result({"sql": "SELECT 1"}))


def test_not_applicable_without_materialized_gold() -> None:
    grader = ResultSetMatchGrader()
    assert not grader.applicable(_item({"sql": "SELECT 1"}), _result(_push([[1]])))


def test_matching_rows_pass_exact() -> None:
    exact, facts = _grade(_gold([[1], [2]]), _push([[2], [1]]))

    assert exact.bool_value is True
    assert facts.bool_value is True
    assert exact.raw_output["evidence_complete"] is True


def test_wrong_values_fail_with_a_named_cell() -> None:
    exact, _ = _grade(_gold([[1]]), _push([[3]]))

    assert exact.bool_value is False
    assert exact.raw_output["mismatch"]["kind"] == "values"


def test_row_count_uses_true_counts_not_preview_length() -> None:
    """A 100-row preview of a 500-row answer against 400-row gold fails on count."""
    exact, facts = _grade(
        _gold([[i] for i in range(10)], row_count=400),
        _push([[i] for i in range(10)], row_count=500),
    )

    assert exact.bool_value is False
    assert facts.bool_value is False
    assert exact.raw_output["mismatch"]["kind"] == "row_count"


def test_truncated_evidence_grades_by_containment_and_says_so() -> None:
    """Counts agree, push holds a preview: visible rows must all be in gold."""
    gold_rows = [[i] for i in range(200)]
    exact, facts = _grade(
        _gold(gold_rows, row_count=200),
        _push([[i] for i in range(100)], row_count=200),
    )

    assert exact.bool_value is True
    assert facts.bool_value is True
    assert exact.raw_output["evidence_truncated"] is True


def test_truncated_evidence_with_a_foreign_row_fails() -> None:
    gold_rows = [[i] for i in range(200)]
    exact, _ = _grade(
        _gold(gold_rows, row_count=200),
        _push([[999999]], row_count=200),
    )

    assert exact.bool_value is False


def test_extra_columns_fail_exact_but_pass_got_facts() -> None:
    exact, facts = _grade(
        _gold([["04"]]),
        _push([["04", 126047744.0]], columns=["month", "total"]),
    )

    assert exact.bool_value is False
    assert exact.raw_output["mismatch"]["kind"] == "column_arity"
    assert facts.bool_value is True


def test_mnemiq_report_shape_rows_as_dicts() -> None:
    """mnemiq previews are lists of {column: value} dicts; both sides parse."""
    exact, _ = _grade(
        _gold([[1, "x"]], columns=["id", "name"]),
        _push([{"id": 1, "name": "x"}]),
    )

    assert exact.bool_value is True
    assert exact.raw_output["candidate_columns"] == ["id", "name"]


def test_cross_engine_value_spellings_canonicalize() -> None:
    """A date object on one side and its ISO string on the other still match."""
    from datetime import date

    exact, _ = _grade(
        _gold([["2013-06-01", 5.0]], columns=["d", "n"]),
        _push([[date(2013, 6, 1), 5]]),
    )

    assert exact.bool_value is True


def test_null_matches_only_null() -> None:
    exact_null, _ = _grade(_gold([[None]]), _push([[None]]))
    exact_zero, _ = _grade(_gold([[None]]), _push([[0]]))
    exact_empty, _ = _grade(_gold([[None]]), _push([[""]]))

    assert exact_null.bool_value is True
    assert exact_zero.bool_value is False
    assert exact_empty.bool_value is False


def test_order_matters_when_gold_orders() -> None:
    gold = _gold([[1], [2]], sql="SELECT a FROM t ORDER BY a")
    exact_wrong_order, _ = _grade(gold, _push([[2], [1]]))
    exact_right_order, _ = _grade(gold, _push([[1], [2]]))

    assert exact_wrong_order.bool_value is False
    assert exact_right_order.bool_value is True


def test_engine_and_portability_ride_into_the_evidence() -> None:
    exact, _ = _grade(
        _gold([[1]]),
        _push([[1]], engine="duckdb", portable_to_gold_engine=False),
    )

    assert exact.raw_output["engine"] == "duckdb"
    assert exact.raw_output["portable_to_gold_engine"] is False
