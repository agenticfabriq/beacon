"""Result-set comparison: the engine-independent core both SQL graders share.

Everything here is a pure function over rows. The execution grader feeds it
rows it just fetched from one engine; the result-set grader feeds it rows the
runner pushed plus gold materialized at import. Neither changes the semantics:
exact match decides the outcome, the tolerant projection reading is got-facts.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from itertools import combinations
from typing import Any

from beacon_runner.transport import transport_value

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
    Decimals become floats (the shared transport rule, one home in
    beacon_runner), strings are stripped, and NULL stays NULL (never 0, never
    the empty string). Bools stay bools -- True is not 1.
    """
    if value is None or isinstance(value, bool):
        return value
    transported = transport_value(value)
    if isinstance(transported, float | int):
        return transported
    if transported is not value:
        return transported  # a temporal value, already an ISO string
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
    """Compare one cell, numbers within tolerance and everything else exactly.

    A truth value never matches a number: Python would say True == 1, which
    scores a boolean column against a count. Found by conformance-contract v2
    on the day the case was added -- the same way v1 caught the relative
    bound's off-by-one.
    """
    if isinstance(candidate, bool) != isinstance(gold, bool):
        return False
    if is_number(candidate) and is_number(gold):
        return tolerance.numbers_match(float(candidate), float(gold))
    return bool(candidate == gold)


def is_rounding_of(value: float, other: float) -> bool:
    """Whether ``value`` is ``other`` rounded to some number of decimal places.

    Under either rounding convention: engines disagree on halves (Python's
    ``round`` is banker's, most SQL engines round half up), and 38.13 for
    38.125 is the same quantity as 38.12 -- which convention an engine uses
    is presentation, and presentation must not decide correctness. ``Decimal``
    from ``str`` so binary float noise does not reintroduce itself.

    Rounding to zero places is presentation only for magnitudes of at least
    one: 53 for 52.63 is a rounding, but 0.0 for 0.196 collapses a small
    quantity to nothing -- that answer did not get the fact. (Found by the k12
    disagreement study, where the zero-place case credited exactly that.)
    """
    start = 0 if abs(other) >= 1.0 else 1
    exact = Decimal(str(other))
    for places in range(start, 7):
        step = Decimal(1).scaleb(-places)
        if abs(float(exact.quantize(step, rounding=ROUND_HALF_EVEN)) - value) <= 1e-9:
            return True
        if abs(float(exact.quantize(step, rounding=ROUND_HALF_UP)) - value) <= 1e-9:
            return True
    return False


def facts_values_match(candidate: Any, gold: Any, tolerance: Tolerance) -> bool:
    """The got-facts cell reading: tolerance, or a decimal rounding either way.

    A candidate that returns 66.62 where gold computes 66.6230, or the
    unrounded value where gold applies ROUND(x, 3), got the fact -- the
    quantity is the same, the presentation differs. Exact match stays strict
    (BIRD compares values exactly); this leniency belongs to the second
    metric, which exists to say "right data, different shape".
    """
    if values_match(candidate, gold, tolerance):
        return True
    if is_number(candidate) and is_number(gold):
        cand, ref = float(candidate), float(gold)
        return is_rounding_of(cand, ref) or is_rounding_of(ref, cand)
    return False



CellMatch = Any  # Callable[[Any, Any, Tolerance], bool]; kept loose for mypy simplicity


def row_matches(
    candidate: tuple[Any, ...],
    gold: tuple[Any, ...],
    tolerance: Tolerance,
    cell_match: CellMatch = values_match,
) -> bool:
    """Compare one row position-wise."""
    if len(candidate) != len(gold):
        return False
    return all(cell_match(c, g, tolerance) for c, g in zip(candidate, gold, strict=True))


def rows_match(
    candidate: list[tuple[Any, ...]],
    gold: list[tuple[Any, ...]],
    tolerance: Tolerance,
    cell_match: CellMatch = values_match,
) -> bool:
    """Compare two equally long row lists position-wise."""
    return all(
        row_matches(c, g, tolerance, cell_match) for c, g in zip(candidate, gold, strict=True)
    )


def compare_rows(
    candidate: list[tuple[Any, ...]],
    gold: list[tuple[Any, ...]],
    order_sensitive: bool,
    tolerance: Tolerance | None = None,
    cell_match: CellMatch = values_match,
) -> bool:
    """Whether two result sets state the same rows, order-aware on request."""
    tol = tolerance or Tolerance()
    if len(candidate) != len(gold):
        return False
    if order_sensitive:
        return rows_match(candidate, gold, tol, cell_match)
    try:
        ordered_candidate = sorted(candidate, key=sort_key)
        ordered_gold = sorted(gold, key=sort_key)
    except TypeError:
        return Counter(map(repr, candidate)) == Counter(map(repr, gold))
    return rows_match(ordered_candidate, ordered_gold, tol, cell_match)


def contains_rows(
    candidate: list[tuple[Any, ...]],
    gold: list[tuple[Any, ...]],
    tolerance: Tolerance | None = None,
    cell_match: CellMatch = values_match,
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
            if row_matches(row, gold_row, tol, cell_match):
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
    tolerance: Tolerance,
) -> bool:
    """Whether the gold's data is present, allowing extra candidate columns.

    mnemiq's CORRECT_FACTS, computed here so the second metric is beacon's
    own claim rather than the runner grading itself. The candidate may add
    context columns but never omit a gold column, extra columns cannot
    rescue wrong rows, and neither column order nor row order is meaning --
    got-facts asks whether the data is there, and ordering is shape. (Row
    order also varies at tied ORDER BY keys across engines, which is
    tie-breaking, not a different answer.) Exact match stays order-aware.
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
        if compare_rows(projected, gold.rows, False, tolerance, facts_values_match):
            return True
        if compare_rows(_sort_cells(projected), gold_sorted, False, tolerance, facts_values_match):
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
        if contains_rows(projected, gold.rows, tolerance, facts_values_match):
            return True
        if contains_rows(
            _sort_cells(projected), gold_cells_sorted, tolerance, facts_values_match
        ):
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
    # Same cells row for row once each row's cells are sorted: the values are
    # all there and only SELECT order differs. Exact match is position-wise
    # (BIRD's published metric is), so this fails -- but it should say why.
    if compare_rows(
        _sort_cells(candidate.rows), _sort_cells(gold.rows), order_sensitive, tolerance
    ):
        return Mismatch("column_order", "same values, columns selected in a different order")
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
