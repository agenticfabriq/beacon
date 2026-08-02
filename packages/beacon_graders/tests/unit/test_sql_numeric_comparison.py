"""Numeric results compare as numbers, not as Python objects (B14).

A candidate that casts to DECIMAL and a gold that casts to REAL return the same
quantity in different types. Compared with ``==`` those are unequal, so a
numerically correct answer scored FAIL -- observed live on a BIRD ratio item.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy as sa
from beacon_graders.graders import ExecutionGroundedSqlGrader
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep

if TYPE_CHECKING:
    from collections.abc import Sequence


@pytest.fixture
def grader() -> ExecutionGroundedSqlGrader:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    return ExecutionGroundedSqlGrader(engine_factory=lambda _item: engine)


def _compare(
    grader: ExecutionGroundedSqlGrader,
    candidate: Sequence[tuple[Any, ...]],
    gold: Sequence[tuple[Any, ...]],
    *,
    order_sensitive: bool = False,
) -> bool:
    return grader._compare(list(candidate), list(gold), order_sensitive)  # noqa: SLF001


def test_decimal_and_float_of_the_same_value_match(
    grader: ExecutionGroundedSqlGrader,
) -> None:
    """The exact live failure: DECIMAL cast against REAL cast."""
    candidate = [(Decimal("0.90490797546012269939"),)]
    gold = [(0.904908,)]

    assert _compare(grader, candidate, gold) is True


def test_decimal_and_int_of_the_same_value_match(
    grader: ExecutionGroundedSqlGrader,
) -> None:
    assert _compare(grader, [(Decimal("58"),)], [(58,)]) is True


def test_genuinely_different_numbers_still_fail(
    grader: ExecutionGroundedSqlGrader,
) -> None:
    assert _compare(grader, [(0.904908,)], [(0.914908,)]) is False


def test_a_difference_beyond_the_precision_still_fails(
    grader: ExecutionGroundedSqlGrader,
) -> None:
    """Tolerance is bounded: 1e-5 apart is still two different answers."""
    assert _compare(grader, [(0.9049080,)], [(0.9049180,)]) is False


def test_strings_are_never_coerced(grader: ExecutionGroundedSqlGrader) -> None:
    """The CDSCode failure must stay a failure."""
    assert _compare(grader, [("0123000",)], [("01100170109835",)]) is False


def test_numeric_strings_are_not_treated_as_numbers(
    grader: ExecutionGroundedSqlGrader,
) -> None:
    assert _compare(grader, [("58",)], [(58,)]) is False


def test_booleans_are_left_out_of_the_numeric_path(
    grader: ExecutionGroundedSqlGrader,
) -> None:
    """Bools never enter the numeric-tolerance path.

    They still compare equal to 1/0 through Python's own ``==``, which predates
    this and is the tolerant reading given drivers disagree on how a boolean
    column comes back. What this pins is that a bool is never treated as a
    number to be compared within a tolerance.
    """
    from beacon_graders.graders.execution_grounded_sql import _is_number

    assert _is_number(True) is False
    assert _is_number(False) is False
    assert _is_number(1.0) is True


def test_extra_columns_still_fail(grader: ExecutionGroundedSqlGrader) -> None:
    """The over-selection failure must stay a failure."""
    candidate = [(1, "Pacific Collegiate Charter", "0210", 630)]
    gold = [("0210", 630, 1)]

    assert _compare(grader, candidate, gold) is False


def test_differing_row_counts_still_fail(grader: ExecutionGroundedSqlGrader) -> None:
    assert _compare(grader, [(1.0,), (2.0,)], [(1.0,)]) is False


def test_nulls_are_preserved(grader: ExecutionGroundedSqlGrader) -> None:
    assert _compare(grader, [(None,)], [(0.0,)]) is False
    assert _compare(grader, [(None,)], [(None,)]) is True


def test_order_sensitivity_is_unaffected(grader: ExecutionGroundedSqlGrader) -> None:
    candidate = [(Decimal("2"),), (Decimal("1"),)]
    gold = [(1.0,), (2.0,)]

    assert _compare(grader, candidate, gold, order_sensitive=True) is False
    assert _compare(grader, candidate, gold, order_sensitive=False) is True


def test_end_to_end_ratio_item_passes() -> None:
    """Through grade(), against a real engine, with divergent casts."""
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE t (num REAL, den REAL)")
        conn.exec_driver_sql("INSERT INTO t VALUES (295.0, 326.0)")

    grader = ExecutionGroundedSqlGrader(engine_factory=lambda _item: engine)
    item = EvalItem(
        item_id="ratio-1",
        suite="s",
        query={"question": "highest rate"},
        # Same quantity, printed to fewer places -- the gold's REAL cast.
        ground_truth={"sql": "SELECT ROUND(MAX(num / den), 6) FROM t"},
        metadata={},
    )
    result = ExecutionResult(
        output={"sql": "SELECT MAX(num / den) FROM t"},
        output_kind="sql",
        trace=ExecutionStep(uuid="r", name="r", level="workflow"),
    )

    verdicts = grader.grade(item, result)

    assert verdicts[0].bool_value is True, verdicts[0].justification
