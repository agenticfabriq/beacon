"""Curated per-item tolerance beats beacon's global default (B23).

B14 fixed Decimal-vs-REAL by rounding every comparison to a constant I picked
while debugging one BIRD item. The semantic layer curates a reviewed tolerance
per question. Flattening that into the constant would silently override a
decision somebody made deliberately.
"""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from beacon_graders.graders import ExecutionGroundedSqlGrader
from beacon_graders.tolerance import DEFAULT_NUMERIC_ABS, Tolerance
from beacon_runner.types import EvalItem


def _item(**metadata: object) -> EvalItem:
    return EvalItem(
        item_id="i-0",
        suite="s",
        query={},
        ground_truth={"sql": "SELECT 1"},
        metadata=dict(metadata),
    )


def _grader() -> ExecutionGroundedSqlGrader:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    return ExecutionGroundedSqlGrader(engine_factory=lambda _i: engine)


def test_an_item_without_curated_tolerance_uses_the_default() -> None:
    assert Tolerance.for_item(_item()).numeric_abs == DEFAULT_NUMERIC_ABS


def test_a_curated_tolerance_is_honoured() -> None:
    tol = Tolerance.for_item(_item(tolerance={"numeric_abs": 0.01}))

    assert tol.numeric_abs == 0.01


def test_a_looser_curated_tolerance_admits_a_wider_difference() -> None:
    """Two cents apart passes at a curated 0.05 and fails at the default."""
    grader = _grader()
    candidate, gold = [(10.02,)], [(10.00,)]

    loose = Tolerance(numeric_abs=0.05)
    assert grader._compare(candidate, gold, False, loose) is True  # noqa: SLF001
    assert grader._compare(candidate, gold, False, Tolerance()) is False  # noqa: SLF001


def test_a_tighter_curated_tolerance_rejects_what_the_default_admits() -> None:
    """Curation can be stricter than beacon, not only looser."""
    grader = _grader()
    candidate, gold = [(1.0000001,)], [(1.0,)]

    assert grader._compare(candidate, gold, False, Tolerance()) is True  # noqa: SLF001
    # Two bounds now, and either one satisfies a match, so exact means both zero.
    strict = Tolerance(numeric_abs=0.0, numeric_rel=0.0)
    assert grader._compare(candidate, gold, False, strict) is False  # noqa: SLF001


def test_the_decimal_versus_real_case_still_passes() -> None:
    """B14's live failure, now expressed as an absolute difference."""
    grader = _grader()
    candidate = [(Decimal("0.90490797546012269939"),)]
    gold = [(0.904908,)]

    assert grader._compare(candidate, gold, False, Tolerance()) is True  # noqa: SLF001


def test_curated_row_order_insensitivity_overrides_the_order_by_heuristic() -> None:
    """Beacon infers order sensitivity from ORDER BY; curated gold can say."""
    item = _item(tolerance={"row_order_insensitive": True})

    assert Tolerance.for_item(item).row_order_insensitive is True


def test_no_curated_opinion_leaves_the_heuristic_alone() -> None:
    assert Tolerance.for_item(_item()).row_order_insensitive is None


def test_malformed_tolerance_metadata_falls_back_rather_than_raising() -> None:
    """Imported metadata is external data; a bad value must not fail a run."""
    assert Tolerance.for_item(_item(tolerance="nonsense")).numeric_abs == DEFAULT_NUMERIC_ABS
    assert Tolerance.for_item(_item(tolerance=None)).numeric_abs == DEFAULT_NUMERIC_ABS


def test_tolerance_never_loosens_non_numeric_comparison() -> None:
    """Strings, arity and row counts stay exact however loose the numbers are."""
    grader = _grader()
    loose = Tolerance(numeric_abs=1e9)

    assert grader._compare([("a",)], [("b",)], False, loose) is False  # noqa: SLF001
    assert grader._compare([(1, 2)], [(1,)], False, loose) is False  # noqa: SLF001
    assert grader._compare([(1.0,), (2.0,)], [(1.0,)], False, loose) is False  # noqa: SLF001


def test_a_large_answer_cannot_satisfy_an_absolute_bound_alone() -> None:
    """A REAL holds ~7 significant digits; a 9-digit answer cannot land within 5e-7.

    Measured on the re-graded corpus: 45 of 350 value mismatches were this --
    correct answers failing on representation noise.
    """
    tolerance = Tolerance()

    assert tolerance.numbers_match(199.6415615081787, 199.6415625) is True


def test_accumulated_float32_error_is_still_a_failure() -> None:
    """The default admits representation noise, not error built up over a sum.

    A suite that wants to accept it says so per item; that is what curated
    tolerance is for.
    """
    tolerance = Tolerance()

    assert tolerance.numbers_match(402531650.0, 402524570.0228404) is False


def test_a_genuinely_different_number_stays_different() -> None:
    tolerance = Tolerance()

    assert tolerance.numbers_match(75.28125, 75.39393939393939) is False


def test_small_magnitudes_still_use_the_absolute_bound() -> None:
    """Near zero a relative bound is vanishingly tight, so the absolute one carries."""
    tolerance = Tolerance()

    assert tolerance.numbers_match(0.0, 4e-7) is True
    assert tolerance.numbers_match(0.0, 4e-3) is False


def test_a_curated_relative_tolerance_is_honoured() -> None:
    tolerance = Tolerance(numeric_rel=1e-4)

    assert tolerance.numbers_match(402531650.0, 402524570.0228404) is True
