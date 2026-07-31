from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from beacon_graders.graders.execution_grounded_sql import ExecutionGroundedSqlGrader

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from beacon_runner.types import EvalItem, ExecutionResult


@pytest.fixture
def engine() -> Iterator[sa.Engine]:
    eng = sa.create_engine("sqlite+pysqlite:///:memory:")
    with eng.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE t (id INTEGER, name TEXT, val INTEGER)")
        connection.exec_driver_sql("INSERT INTO t VALUES (1, 'a', 10), (2, 'b', 20), (3, 'c', 30)")
    yield eng
    eng.dispose()


@pytest.fixture
def grader(engine: sa.Engine) -> ExecutionGroundedSqlGrader:
    def engine_factory(item: EvalItem) -> sa.Engine:  # noqa: ARG001
        return engine

    return ExecutionGroundedSqlGrader(engine_factory=engine_factory)


def test_matching_sql_passes(
    grader: ExecutionGroundedSqlGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"sql": "SELECT name FROM t WHERE val >= 20"})
    result = make_result(
        output={"sql": "SELECT name FROM t WHERE val > 19"},
        output_kind="sql",
    )
    verdict = grader.grade(item, result)[0]
    assert verdict.bool_value is True


def test_mismatched_sql_fails(
    grader: ExecutionGroundedSqlGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"sql": "SELECT name FROM t WHERE val >= 20"})
    result = make_result(
        output={"sql": "SELECT name FROM t"},
        output_kind="sql",
    )
    verdict = grader.grade(item, result)[0]
    assert verdict.bool_value is False


def test_missing_candidate_sql_fails(
    grader: ExecutionGroundedSqlGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"sql": "SELECT 1"})
    result = make_result(output={}, output_kind="sql")
    verdict = grader.grade(item, result)[0]
    assert verdict.bool_value is False
    assert "No SQL" in verdict.justification


def test_missing_gold_sql_fails(
    grader: ExecutionGroundedSqlGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={})
    result = make_result(output={"sql": "SELECT 1"}, output_kind="sql")
    verdict = grader.grade(item, result)[0]
    assert verdict.bool_value is False


def test_order_sensitive_when_gold_has_order_by(
    grader: ExecutionGroundedSqlGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"sql": "SELECT name FROM t ORDER BY val"})
    result_correct = make_result(
        output={"sql": "SELECT name FROM t ORDER BY val ASC"},
        output_kind="sql",
    )
    result_swapped = make_result(
        output={"sql": "SELECT name FROM t ORDER BY val DESC"},
        output_kind="sql",
    )
    assert grader.grade(item, result_correct)[0].bool_value is True
    assert grader.grade(item, result_swapped)[0].bool_value is False


def test_invalid_candidate_sql_produces_error_verdict(
    grader: ExecutionGroundedSqlGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"sql": "SELECT 1"})
    result = make_result(output={"sql": "BOGUS"}, output_kind="sql")
    verdict = grader.grade(item, result)[0]
    assert verdict.bool_value is False
    assert "candidate sql failed" in verdict.justification.lower()


def test_like_pattern_in_gold_sql_grades(
    grader: ExecutionGroundedSqlGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"sql": "SELECT name FROM t WHERE name LIKE '%b%'"})
    result = make_result(
        output={"sql": "SELECT name FROM t WHERE val = 20"},
        output_kind="sql",
    )
    verdict = grader.grade(item, result)[0]
    assert verdict.bool_value is True


def test_percent_is_escaped_for_pyformat_drivers(
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    executed: list[str] = []

    class _Cursor:
        def fetchall(self) -> list[tuple[str]]:
            return [("b",)]

    class _Connection:
        def exec_driver_sql(self, sql: str) -> _Cursor:
            executed.append(sql)
            return _Cursor()

        def __enter__(self) -> _Connection:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class _Dialect:
        paramstyle = "pyformat"

    class _Engine:
        dialect = _Dialect()

        def connect(self) -> _Connection:
            return _Connection()

    grader = ExecutionGroundedSqlGrader(engine_factory=lambda _item: _Engine())  # type: ignore[arg-type, return-value]
    item = make_item(ground_truth={"sql": "SELECT name FROM t WHERE name LIKE '%b%'"})
    result = make_result(output={"sql": "SELECT name FROM t WHERE val = 20"}, output_kind="sql")

    verdict = grader.grade(item, result)[0]
    assert verdict.bool_value is True
    assert executed == [
        "SELECT name FROM t WHERE val = 20",
        "SELECT name FROM t WHERE name LIKE '%%b%%'",
    ]


def test_applicable_only_for_sql_outputs(
    grader: ExecutionGroundedSqlGrader,
    make_item: Callable[..., EvalItem],
    make_result: Callable[..., ExecutionResult],
) -> None:
    item = make_item(ground_truth={"sql": "SELECT 1"})
    sql_result = make_result(output={"sql": "x"}, output_kind="sql")
    json_result = make_result(output={"answer": "x"}, output_kind="answer")
    assert grader.applicable(item, sql_result) is True
    assert grader.applicable(item, json_result) is False
