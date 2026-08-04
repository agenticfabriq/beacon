"""Result-set comparison: the engine-independent core both SQL graders share.

Everything here is a pure function over rows. The execution grader feeds it
rows it just fetched from one engine; the result-set grader feeds it rows the
runner pushed plus gold materialized at import. Neither changes the semantics:
exact match decides the outcome, the tolerant projection reading is got-facts.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from itertools import combinations
from typing import Any

from beacon_graders.tolerance import Tolerance

# How many rows of each side to keep on the verdict. Enough to show a reader
# what the two answers looked like, bounded so a 7,806-row gold cannot bloat
# every verdict row that references it.
SAMPLE_ROWS = 5

# Trying every way to project a wide candidate onto the gold's arity is
# combinatorial; past this many projections the tolerant reading gives up and
# says so, rather than stalling the grader on a pathological SELECT *.
MAX_PROJECTIONS = 100


def is_number(value: Any) -> bool:
    """Return whether a cell is a real number. Bools are not: True is not 1."""
    return isinstance(value, Decimal | float | int) and not isinstance(value, bool)


def sort_key(row: tuple[Any, ...]) -> tuple[str, ...]:
    """Order rows for order-insensitive comparison without comparing mixed types."""
    return tuple(repr(v) for v in row)


def jsonable(value: Any) -> Any:
    """Coerce a database cell to something JSONB can hold, losing nothing visible."""
    if value is None or isinstance(value, bool | float | int | str):
        return value
    return str(value)


def canonicalize_cell(value: Any) -> Any:
    """Reduce a cell to what it means, discarding how its engine spelled it.

    Rows that crossed an engine or JSON boundary no longer share one driver's
    representations, so before comparison: temporal values become ISO strings,
    Decimals become floats, strings are stripped, and NULL stays NULL (never
    0, never the empty string). Bools stay bools -- True is not 1.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float | int):
        return value
    return str(value).strip()


def canonicalize_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """Canonicalize every cell of every row."""
    return [tuple(canonicalize_cell(cell) for cell in row) for row in rows]


@dataclass(frozen=True)
class ResultSet:
    """The rows a query returned, and the names of their columns."""

    columns: list[str]
    rows: list[tuple[Any, ...]]

    def sample(self, limit: int = SAMPLE_ROWS) -> list[list[Any]]:
        """Return the first ``limit`` rows, JSON-safe."""
        return [[jsonable(cell) for cell in row] for row in self.rows[:limit]]


@dataclass(frozen=True)
class Mismatch:
    """Which dimension of a comparison actually differed.

    The old justification named row counts even when the counts were equal,
    which described the one thing that was not wrong and left every value
    mismatch to be diagnosed by re-executing both queries by hand.
    """

    kind: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        """Render for storage on the verdict."""
        return {"kind": self.kind, "detail": self.detail}


def values_match(candidate: Any, gold: Any, tolerance: Tolerance) -> bool:
    """Compare one cell, numbers within tolerance and everything else exactly."""
    if is_number(candidate) and is_number(gold):
        return tolerance.numbers_match(float(candidate), float(gold))
    return bool(candidate == gold)


def row_matches(
    candidate: tuple[Any, ...],
    gold: tuple[Any, ...],
    tolerance: Tolerance,
) -> bool:
    """Compare one row position-wise."""
    if len(candidate) != len(gold):
        return False
    return all(values_match(c, g, tolerance) for c, g in zip(candidate, gold, strict=True))


def rows_match(
    candidate: list[tuple[Any, ...]],
    gold: list[tuple[Any, ...]],
    tolerance: Tolerance,
) -> bool:
    """Compare two equally long row lists position-wise."""
    return all(row_matches(c, g, tolerance) for c, g in zip(candidate, gold, strict=True))


def compare_rows(
    candidate: list[tuple[Any, ...]],
    gold: list[tuple[Any, ...]],
    order_sensitive: bool,
    tolerance: Tolerance | None = None,
) -> bool:
    """Whether two result sets state the same rows, order-aware on request."""
    tol = tolerance or Tolerance()
    if len(candidate) != len(gold):
        return False
    if order_sensitive:
        return rows_match(candidate, gold, tol)
    try:
        ordered_candidate = sorted(candidate, key=sort_key)
        ordered_gold = sorted(gold, key=sort_key)
    except TypeError:
        return Counter(map(repr, candidate)) == Counter(map(repr, gold))
    return rows_match(ordered_candidate, ordered_gold, tol)


def contains_rows(
    candidate: list[tuple[Any, ...]],
    gold: list[tuple[Any, ...]],
    tolerance: Tolerance | None = None,
) -> bool:
    """Whether every candidate row matches a distinct gold row (multiset).

    The truncated-evidence reading: a pushed preview cannot be compared row
    count for row count, so each pushed row must be accounted for in gold and
    the true counts are checked separately by the caller.
    """
    tol = tolerance or Tolerance()
    remaining = list(gold)
    for row in candidate:
        for index, gold_row in enumerate(remaining):
            if row_matches(row, gold_row, tol):
                del remaining[index]
                break
        else:
            return False
    return True


def _sort_cells(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    return [tuple(sorted(row, key=repr)) for row in rows]


def got_facts(
    candidate: ResultSet,
    gold: ResultSet,
    order_sensitive: bool,
    tolerance: Tolerance,
) -> bool:
    """Whether the gold's data is present, allowing extra candidate columns.

    mnemiq's CORRECT_FACTS, computed here so the second metric is beacon's
    own claim rather than the runner grading itself. The candidate may add
    context columns but never omit a gold column, extra columns cannot
    rescue wrong rows, and column order is not meaning -- each projection is
    also retried with every row's cells in a canonical order.
    """
    if len(candidate.rows) != len(gold.rows):
        return False
    gold_arity = len(gold.rows[0]) if gold.rows else len(gold.columns)
    candidate_arity = len(candidate.rows[0]) if candidate.rows else len(candidate.columns)
    if gold_arity > candidate_arity:
        return False

    gold_sorted = _sort_cells(gold.rows)
    for index, keep in enumerate(combinations(range(candidate_arity), gold_arity)):
        if index >= MAX_PROJECTIONS:
            return False
        projected = [tuple(row[i] for i in keep) for row in candidate.rows]
        if compare_rows(projected, gold.rows, order_sensitive, tolerance):
            return True
        if compare_rows(_sort_cells(projected), gold_sorted, order_sensitive, tolerance):
            return True
    return False


def got_facts_contained(
    candidate: ResultSet,
    gold: ResultSet,
    tolerance: Tolerance,
) -> bool:
    """The tolerant reading over truncated evidence: projected containment.

    Every pushed row, under some projection onto gold's arity, must match a
    distinct gold row. The caller has already required the true row counts to
    agree; this checks that nothing in the visible preview contradicts gold.
    """
    gold_arity = len(gold.rows[0]) if gold.rows else len(gold.columns)
    candidate_arity = len(candidate.rows[0]) if candidate.rows else len(candidate.columns)
    if gold_arity > candidate_arity:
        return False
    gold_cells_sorted = _sort_cells(gold.rows)
    for index, keep in enumerate(combinations(range(candidate_arity), gold_arity)):
        if index >= MAX_PROJECTIONS:
            return False
        projected = [tuple(row[i] for i in keep) for row in candidate.rows]
        if contains_rows(projected, gold.rows, tolerance):
            return True
        if contains_rows(_sort_cells(projected), gold_cells_sorted, tolerance):
            return True
    return False


def diagnose(
    candidate: ResultSet,
    gold: ResultSet,
    order_sensitive: bool,
    tolerance: Tolerance,
) -> Mismatch:
    """Name the dimension that differed, and the first place it differed."""
    if len(candidate.rows) != len(gold.rows):
        return Mismatch(
            "row_count",
            f"candidate returned {len(candidate.rows)} rows, gold returned {len(gold.rows)}",
        )
    left, right = candidate.rows, gold.rows
    if not order_sensitive:
        try:
            left, right = sorted(left, key=sort_key), sorted(right, key=sort_key)
        except TypeError:
            return Mismatch("values", "rows differ; mixed types prevent an ordered comparison")
    for index, (row, gold_row) in enumerate(zip(left, right, strict=True)):
        if len(row) != len(gold_row):
            return Mismatch(
                "column_arity",
                f"row {index} has {len(row)} columns "
                f"({', '.join(candidate.columns) or 'unnamed'}), "
                f"gold has {len(gold_row)} ({', '.join(gold.columns) or 'unnamed'})",
            )
        for column, (cell, gold_cell) in enumerate(zip(row, gold_row, strict=True)):
            if not values_match(cell, gold_cell, tolerance):
                name = candidate.columns[column] if column < len(candidate.columns) else column
                return Mismatch(
                    "values",
                    f"row {index}, column {name!r}: candidate {cell!r} != gold {gold_cell!r}",
                )
    return Mismatch("values", "rows differ under the comparison but no differing cell was found")
