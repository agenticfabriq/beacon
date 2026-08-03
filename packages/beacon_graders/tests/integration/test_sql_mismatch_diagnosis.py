"""A failing verdict has to say what was wrong with it.

The old message named row counts even when the counts matched, so every value
mismatch had to be diagnosed by re-executing both queries by hand. These pin
the three dimensions a result comparison can fail on, and that both answers are
kept on the verdict so a reader never has to re-run anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy as sa
from beacon_graders.graders.execution_grounded_sql import SAMPLE_ROWS, ExecutionGroundedSqlGrader
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration


@pytest.fixture
def engine() -> Iterator[sa.Engine]:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE t (a INTEGER, b TEXT, c REAL)")
        for row in [(1, "x", 1.0), (2, "y", 2.0), (3, "z", 3.0)]:
            connection.exec_driver_sql("INSERT INTO t VALUES (?, ?, ?)", row)
    yield engine
    engine.dispose()


@pytest.fixture
def grader(engine: sa.Engine) -> ExecutionGroundedSqlGrader:
    return ExecutionGroundedSqlGrader(engine_factory=lambda _item: engine)


def _item(gold_sql: str) -> EvalItem:
    return EvalItem(item_id="i", suite="s", query={"question": "q"}, ground_truth={"sql": gold_sql})


def _result(sql: str) -> ExecutionResult:
    return ExecutionResult(
        output={"sql": sql},
        output_kind="sql",
        trace=ExecutionStep(uuid="u", name="n", level="workflow"),
    )


def _raw(grader: ExecutionGroundedSqlGrader, candidate: str, gold: str) -> dict[str, Any]:
    verdict = grader.grade(_item(gold), _result(candidate))[0]
    assert verdict.raw_output is not None
    return verdict.raw_output


def test_a_row_count_difference_is_named_as_one(grader: ExecutionGroundedSqlGrader) -> None:
    raw = _raw(grader, "SELECT a FROM t WHERE a < 3", "SELECT a FROM t")

    assert raw["mismatch"]["kind"] == "row_count"
    assert "2 rows" in raw["mismatch"]["detail"]
    assert "3" in raw["mismatch"]["detail"]


def test_an_extra_column_is_named_as_column_arity(grader: ExecutionGroundedSqlGrader) -> None:
    """The live instance: right rows, right order, one column too many."""
    raw = _raw(grader, "SELECT a, b FROM t", "SELECT a FROM t")

    assert raw["mismatch"]["kind"] == "column_arity"
    assert "2 columns" in raw["mismatch"]["detail"]


def test_a_differing_value_names_the_row_the_column_and_both_values(
    grader: ExecutionGroundedSqlGrader,
) -> None:
    raw = _raw(grader, "SELECT a + 10 FROM t", "SELECT a FROM t")

    assert raw["mismatch"]["kind"] == "values"
    assert "row 0" in raw["mismatch"]["detail"]
    assert "11" in raw["mismatch"]["detail"]
    assert "1" in raw["mismatch"]["detail"]


def test_a_match_records_no_mismatch(grader: ExecutionGroundedSqlGrader) -> None:
    verdict = grader.grade(_item("SELECT a FROM t"), _result("SELECT a FROM t"))[0]

    assert verdict.bool_value is True
    assert verdict.raw_output is not None
    assert "mismatch" not in verdict.raw_output


def test_both_answers_are_kept_so_a_reader_need_not_re_execute(
    grader: ExecutionGroundedSqlGrader,
) -> None:
    """Re-running later would answer from today's database, not the graded one."""
    raw = _raw(grader, "SELECT a, b FROM t", "SELECT a FROM t")

    assert raw["candidate_columns"] == ["a", "b"]
    assert raw["gold_columns"] == ["a"]
    assert raw["candidate_sample"][0] == [1, "x"]
    assert raw["gold_sample"][0] == [1]


def test_the_sample_is_bounded(grader: ExecutionGroundedSqlGrader, engine: sa.Engine) -> None:
    """A 7,806-row gold must not be copied onto every verdict that references it."""
    with engine.begin() as connection:
        for value in range(4, 40):
            connection.exec_driver_sql("INSERT INTO t VALUES (?, ?, ?)", (value, "w", 0.0))

    raw = _raw(grader, "SELECT a FROM t WHERE a < 100", "SELECT a FROM t WHERE a < 99")

    assert len(raw["candidate_sample"]) == SAMPLE_ROWS
    assert raw["candidate_row_count"] > SAMPLE_ROWS


def test_a_sample_cell_survives_json_storage(grader: ExecutionGroundedSqlGrader) -> None:
    """raw_output is JSONB; a Decimal or a date would fail to serialise."""
    raw = _raw(grader, "SELECT c FROM t", "SELECT a FROM t")

    for row in raw["candidate_sample"]:
        for cell in row:
            assert cell is None or isinstance(cell, bool | float | int | str)
