"""The pruned got-facts search gives the verdict the full search would, and decides wide answers.

The old search tried every projection of the candidate's columns onto gold's and stopped at 100,
scoring FAIL past it, so a wide answer that held the facts was graded wrong (mnemiq register
M113). The reference below is that search WITHOUT the cap; on every sampled case the pruned search
must agree with it, and no case may come back undecided at these sizes.
"""

from __future__ import annotations

import random
from itertools import combinations
from typing import Any

import pytest
from beacon_graders import comparison, projection_search
from beacon_graders.comparison import (
    ResultSet,
    _sort_cells,
    compare_rows,
    contains_rows,
    facts_values_match,
    got_facts,
    got_facts_contained,
)
from beacon_graders.projection_search import ProjectionSearchUndecided
from beacon_graders.tolerance import Tolerance

TOL = Tolerance()
# Values chosen to sit on every edge the reading has: whole and fractional numbers one rounding
# apart, values inside and outside the tolerance, a bool beside 1, NULL, strings, mixed types.
# 1000000002 is inside the default relative bound of 1000000000.5 but is not its rounding; 0.45
# as gold is within a relative bound of 0.6 of 0.2, further than the narrow-bound window reaches.
_POOL = [
    0,
    1,
    1.0,
    2,
    1.25,
    1.2,
    1.3,
    1.249,
    0.196,
    0.2,
    0.45,
    0.0,
    52.63,
    53,
    53.0,
    1e9,
    1000000000.5,
    1000000002,
    2.0000001,
    True,
    False,
    None,
    "a",
    "b",
    "1",
    " a",
]
# The default bounds; an absolute bound wide enough to reach a neighbour; a relative bound of 1/2
# or more, which the search treats as reaching every number.
_TOLERANCES = [TOL, Tolerance(numeric_abs=0.06, numeric_rel=0.0), Tolerance(numeric_rel=0.6)]


def _full_search(candidate: ResultSet, gold: ResultSet, contained: bool, tol: Tolerance) -> bool:
    """The search as it was, minus the 100-projection cap."""
    if not contained and len(candidate.rows) != len(gold.rows):
        return False
    arity = len(gold.rows[0]) if gold.rows else len(gold.columns)
    width = len(candidate.rows[0]) if candidate.rows else len(candidate.columns)
    if arity > width or arity == 0:
        return False
    gold_sorted = _sort_cells(gold.rows)
    for keep in combinations(range(width), arity):
        projected = [tuple(row[i] for i in keep) for row in candidate.rows]
        if contained:
            if contains_rows(projected, gold.rows, tol, facts_values_match):
                return True
            if contains_rows(_sort_cells(projected), gold_sorted, tol, facts_values_match):
                return True
        else:
            if compare_rows(projected, gold.rows, False, tol, facts_values_match):
                return True
            if compare_rows(_sort_cells(projected), gold_sorted, False, tol, facts_values_match):
                return True
    return False


def _case(rng: random.Random) -> tuple[ResultSet, ResultSet]:
    arity = rng.randint(1, 3)
    width = rng.randint(arity, 6)
    n = rng.randint(0, 4)
    gold_rows = [tuple(rng.choice(_POOL) for _ in range(arity)) for _ in range(n)]
    # Half the time build the candidate from gold, so matches are common; then perturb it.
    if rng.random() < 0.5 and gold_rows:
        cols = sorted(rng.sample(range(width), arity))
        cand_rows = []
        for g in gold_rows:
            row = [rng.choice(_POOL) for _ in range(width)]
            for j, c in enumerate(cols):
                row[c] = g[j]
            cand_rows.append(tuple(row))
        if rng.random() < 0.3:
            r = rng.randrange(len(cand_rows))
            row = list(cand_rows[r])
            row[rng.randrange(width)] = rng.choice(_POOL)
            cand_rows[r] = tuple(row)
        rng.shuffle(cand_rows)
    else:
        cand_rows = [tuple(rng.choice(_POOL) for _ in range(width)) for _ in range(n)]
    gold = ResultSet(columns=[f"g{j}" for j in range(arity)], rows=gold_rows)
    candidate = ResultSet(columns=[f"c{i}" for i in range(width)], rows=cand_rows)
    return candidate, gold


@pytest.mark.parametrize("tol_index", range(len(_TOLERANCES)))
@pytest.mark.parametrize("contained", [False, True])
def test_the_pruned_search_agrees_with_the_full_search(contained: bool, tol_index: int) -> None:
    tol = _TOLERANCES[tol_index]
    rng = random.Random(113 + 10 * tol_index + contained)  # noqa: S311 -- seeded on purpose
    positives = 0
    for _ in range(3000):
        candidate, gold = _case(rng)
        expected = _full_search(candidate, gold, contained, tol)
        actual = (got_facts_contained if contained else got_facts)(candidate, gold, tol)
        assert actual == expected, (candidate, gold)
        positives += expected
    assert positives > 300, "the sample must hold real matches, or agreement proves little"


def test_a_wide_answer_holding_the_facts_is_found() -> None:
    """The case the 100-projection cap scored FAIL: an answer 80 columns wide whose facts sit in
    columns 60, 70 and 79 -- C(80, 3) = 82,160 projections, the match far past the 100th."""
    gold_rows = [(f"k{i}", float(i) + 0.5, i * 3) for i in range(5)]
    cand_rows = []
    for g in gold_rows:
        row: list[Any] = [f"noise{i}-{g[0]}" for i in range(80)]
        row[60], row[70], row[79] = g
        cand_rows.append(tuple(row))
    candidate = ResultSet(columns=[f"c{i}" for i in range(80)], rows=cand_rows)
    gold = ResultSet(columns=["k", "v", "n"], rows=gold_rows)
    assert got_facts(candidate, gold, TOL) is True


def test_a_search_past_its_budget_is_undecided_not_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every column holds both flags, so every pair of columns survives pruning, yet no pair
    holds the facts; with the budget tiny, the search must say it could not decide rather than
    score the answer wrong."""
    monkeypatch.setattr(comparison, "MAX_CHOICES", 3)
    gold = ResultSet(columns=["a", "b"], rows=[(True, False), (False, True)])
    candidate = ResultSet(
        columns=[f"c{i}" for i in range(8)],
        rows=[(True,) * 8, (False,) * 8],
    )
    with pytest.raises(ProjectionSearchUndecided, match="more than 3 projections"):
        got_facts(candidate, gold, TOL)
    # The control: at the shipped budget the same answer is decided, and decided wrong.
    monkeypatch.undo()
    assert got_facts(candidate, gold, TOL) is False


def test_a_bool_never_stands_in_for_a_number() -> None:
    gold = ResultSet(columns=["n"], rows=[(1,)])
    candidate = ResultSet(columns=["flag", "x"], rows=[(True, "z")])
    assert got_facts(candidate, gold, TOL) is False


def test_pruning_past_its_step_budget_is_undecided(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pruning itself is bounded too: a search that spends its steps before reaching any
    projection says so instead of answering."""
    monkeypatch.setattr(projection_search, "MAX_PRUNING_STEPS", 5)
    gold = ResultSet(columns=["a", "b"], rows=[(1, 2), (3, 4)])
    candidate = ResultSet(columns=[f"c{i}" for i in range(6)], rows=[(1, 2) * 3, (3, 4) * 3])
    with pytest.raises(ProjectionSearchUndecided, match="more than 5 steps"):
        got_facts(candidate, gold, TOL)
    monkeypatch.undo()
    assert got_facts(candidate, gold, TOL) is True


def test_cells_the_reading_cannot_compare_leave_it_undecided() -> None:
    """Beacon's reading raises on an infinity against a fractional number (it rounds through
    Decimal). Raising is not a mismatch, so with no other projection matching there is no
    verdict -- but a projection that does match still decides."""
    gold = ResultSet(columns=["g"], rows=[(1.5,)])
    with pytest.raises(ProjectionSearchUndecided, match="cannot compare"):
        got_facts(ResultSet(columns=["a", "b"], rows=[(float("inf"), "x")]), gold, TOL)
    assert got_facts(ResultSet(columns=["a", "b"], rows=[(float("inf"), 1.5)]), gold, TOL) is True
