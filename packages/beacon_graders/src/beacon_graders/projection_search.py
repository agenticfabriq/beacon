"""Which column projections can possibly state gold's rows -- the got-facts search, pruned.

Got-facts asks whether SOME projection of the candidate's columns onto gold's arity states gold's
rows. Trying every one is C(candidate columns, gold columns): 4e12 for a 74-column answer to an
11-column gold. The search used to give up after 100 projections and score FAIL, so a wide answer
that held the facts was graded wrong (mnemiq register M113). Each rule here is a condition EVERY
matching projection meets under beacon's own readings, so dropping a projection that fails one
cannot change a verdict: the survivors are still checked by the same `compare_rows` /
`contains_rows` as before. Past a budget the search does not guess -- it raises
`ProjectionSearchUndecided`, which the graders record as undecided, never FAIL.

Ported from mnemiq's pruned grader (mnemiq #62); the cell reading here is beacon's
`facts_values_match`, whose tolerance is per item.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterator
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import combinations
from typing import Any

# Projections that survive pruning and are checked in full; past this the verdict is undecided.
MAX_CHOICES = 10_000
# Index lookups and cell comparisons the pruning itself may spend.
MAX_PRUNING_STEPS = 2_000_000

CellMatch = Callable[[Any, Any], bool]  # (candidate cell, gold cell) -> match


class ProjectionSearchUndecided(Exception):  # noqa: N818 -- a verdict state, not an error
    """The pruned search could not decide within its budget, or met cells it cannot compare."""


class _Work:
    """A budget of steps that raises ProjectionSearchUndecided when spent."""

    def __init__(self, limit: int, shape: str) -> None:
        self.limit = self.left = limit
        self.shape = shape

    def spend(self) -> None:
        self.left -= 1
        if self.left < 0:
            raise ProjectionSearchUndecided(
                f"the projection pruning spent more than {self.limit:,} steps ({self.shape})"
            )


def _is_number(value: Any) -> bool:
    return isinstance(value, Decimal | float | int) and not isinstance(value, bool)


# At and past this magnitude the rounding rule cannot quantize to six places (Decimal keeps 28
# digits), so `is_rounding_of` raises on it; such values, NaN and infinities are compared outright.
_UNSURE_MAGNITUDE = 1e21


def _unsure(v: float) -> bool:
    return math.isnan(v) or math.isinf(v) or abs(v) >= _UNSURE_MAGNITUDE


def _hashable(value: Any) -> bool:
    try:
        hash(value)
    except TypeError:
        return False
    return True


def _distinct(values: list[Any]) -> list[Any]:
    """Each value once. Keyed by type as well as value: True and 1 are equal to Python, not to
    the readings."""
    seen: set[tuple[type, Any]] = set()
    out: list[Any] = []
    for v in values:
        if not _hashable(v):
            out.append(v)
            continue
        key = (type(v), v)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


class _ValueIndex:
    """Answers "does any value here match `x`?" without comparing `x` to every value.

    Beacon's got-facts cell reading accepts a pair only when: both are non-numbers and equal (a
    bool never matches a number), or both are numbers and either within the item's tolerance
    (exact between whole numbers) or one is the other rounded to 0-6 decimal places (so within
    half a unit of the last place kept). The index looks only there and lets the reading decide
    each pair it finds; a pair the reading cannot compare counts as a possible match, which only
    costs time.
    """

    def __init__(
        self, values: list[Any], work: _Work, numeric_abs: float, numeric_rel: float
    ) -> None:
        self._work = work
        self._abs, self._rel = numeric_abs, numeric_rel
        self._others = {
            (isinstance(v, bool), v) for v in values if not _is_number(v) and _hashable(v)
        }
        self._unhashable = [v for v in values if not _is_number(v) and not _hashable(v)]
        numbers = [v for v in values if _is_number(v)]
        self._unsure = [v for v in numbers if _unsure(float(v))]
        self._nums = sorted({float(v) for v in numbers if not _unsure(float(v))})
        self._fractional = [v for v in self._nums if not v.is_integer()]
        self._by_float: dict[float, list[Any]] = {}
        for v in numbers:
            if not _unsure(float(v)):
                self._by_float.setdefault(float(v), []).append(v)

    def any_match(self, x: Any, cell_match: CellMatch, x_is_gold: bool) -> bool:
        def ok(v: Any) -> bool:
            self._work.spend()
            try:
                return cell_match(v, x) if x_is_gold else cell_match(x, v)
            except (ArithmeticError, ValueError):
                return True  # cannot compare: a possible match, so keep the projection

        def ok_float(f: float) -> bool:
            return any(ok(v) for v in self._by_float.get(f, ()))

        self._work.spend()
        if not _is_number(x):
            if not _hashable(x):
                return any(ok(v) for v in self._unhashable) or any(ok(v) for _, v in self._others)
            return (isinstance(x, bool), x) in self._others or any(ok(v) for v in self._unhashable)
        xf = float(x)
        if any(ok(v) for v in self._unsure):
            return True
        if _unsure(xf):
            return False if math.isnan(xf) else any(ok_float(f) for f in self._nums)
        # Tolerance: abs, or rel of the GOLD value -- widened so either side may be gold. Two whole
        # numbers compare exactly, so a whole x need look only at fractional neighbours. A relative
        # bound of 1/2 or more could reach anywhere, so then every number is a neighbour.
        if self._rel >= 0.5:
            pool, lo, hi = self._nums, -math.inf, math.inf
        else:
            width = self._abs + 2 * self._rel * abs(xf) + 1e-12
            pool, lo, hi = (
                (self._fractional if xf.is_integer() else self._nums),
                xf - width,
                xf + width,
            )
        if self._scan(pool, lo, hi, ok_float):
            return True
        try:
            return self._rounding_match(xf, ok_float)
        except InvalidOperation:
            return any(ok_float(f) for f in self._nums)

    @staticmethod
    def _scan(pool: list[float], lo: float, hi: float, ok: Callable[[float], bool]) -> bool:
        return any(ok(pool[i]) for i in range(bisect_left(pool, lo), bisect_right(pool, hi)))

    def _rounding_match(self, x: float, ok: Callable[[float], bool]) -> bool:
        exact = Decimal(str(x))
        for places in range(0, 7):
            step = Decimal(1).scaleb(-places)
            # Some value is x rounded to `places`...
            for mode in (ROUND_HALF_EVEN, ROUND_HALF_UP):
                r = float(exact.quantize(step, rounding=mode))
                if self._scan(self._nums, r - 2e-9, r + 2e-9, ok):
                    return True
            # ...or x is some value rounded to `places`: x then has at most that many decimals and
            # sits within half a unit of that place of the value.
            if abs(float(exact.quantize(step, rounding=ROUND_HALF_EVEN)) - x) <= 1e-9:
                half = 0.5 * 10.0**-places + 2e-9
                if self._scan(self._fractional, x - half, x + half, ok):
                    return True
        return False


def _column(rows: list[tuple[Any, ...]], index: int) -> list[Any]:
    return _distinct([row[index] for row in rows])


def _all_match(
    values: list[Any], index: _ValueIndex, cell_match: CellMatch, values_are_gold: bool
) -> bool:
    return all(index.any_match(v, cell_match, x_is_gold=values_are_gold) for v in values)


def _increasing_choices(fits: list[list[int]]) -> Iterator[tuple[int, ...]]:
    """Every increasing tuple taking its j-th column from `fits[j]` (ascending), never walking a
    dead end: each level is bounded by the latest column that still leaves room for the levels
    after it, so the walk's work is bounded by what it yields. Iterative, not recursive."""
    latest: list[int] = []
    bound: float = math.inf
    for options in reversed(fits):
        room = [c for c in options if c < bound]
        if not room:
            return
        bound = max(room)
        latest.append(int(bound))
    latest.reverse()
    k = len(fits)
    if k == 0:
        yield ()
        return
    chosen = [0] * k
    nxt = [0] * k
    j = 0
    while j >= 0:
        options = fits[j]
        i = max(nxt[j], bisect_right(options, chosen[j - 1] if j else -1))
        if i < len(options) and options[i] <= latest[j]:
            chosen[j], nxt[j] = options[i], i + 1
            if j == k - 1:
                yield tuple(chosen)
            else:
                j += 1
                nxt[j] = 0
        else:
            j -= 1


def _augment(start: int, edges: list[list[int]], owner: dict[int, int]) -> bool:
    """One augmenting path from gold cell `start` (Kuhn), with an explicit stack."""
    seen: set[int] = set()
    stack = [(start, iter(edges[start]))]
    path: list[tuple[int, int]] = []
    while stack:
        gold, options = stack[-1]
        for c in options:
            if c in seen:
                continue
            seen.add(c)
            path.append((gold, c))
            if c not in owner:
                for g, col in path:
                    owner[col] = g
                return True
            stack.append((owner[c], iter(edges[owner[c]])))
            break
        else:
            stack.pop()
            if path:
                path.pop()
    return False


class Search:
    """The surviving projections for one (candidate, gold) pair under one cell reading."""

    def __init__(
        self,
        cand_rows: list[tuple[Any, ...]],
        gold_rows: list[tuple[Any, ...]],
        width: int,
        arity: int,
        cell_match: CellMatch,
        numeric_abs: float,
        numeric_rel: float,
    ) -> None:
        self.cand_rows, self.gold_rows = cand_rows, gold_rows
        self.width, self.arity = width, arity
        self.cell_match = cell_match
        self.work = _Work(MAX_PRUNING_STEPS, f"{width} candidate columns, {arity} gold")
        self._abs, self._rel = numeric_abs, numeric_rel

    def _index(self, values: list[Any]) -> _ValueIndex:
        return _ValueIndex(values, self.work, self._abs, self._rel)

    def _no_rows(self) -> Iterator[tuple[int, ...]]:
        if self.arity <= self.width:
            yield tuple(range(self.arity))

    def positional(self, both_ways: bool) -> Iterator[tuple[int, ...]]:
        """Increasing projections -- the position-wise reading keeps the candidate's column order --
        whose every column holds only values gold's column holds; and, when `both_ways` (a full
        comparison, rows paired one to one), whose gold column's every value it holds too."""
        if not self.cand_rows:
            yield from self._no_rows()
            return
        gold_cols = [_column(self.gold_rows, j) for j in range(self.arity)]
        cand_cols = [_column(self.cand_rows, c) for c in range(self.width)]
        gold_idx = [self._index(col) for col in gold_cols]
        cand_idx = [self._index(col) for col in cand_cols]
        fits = [
            [
                c
                for c in range(self.width)
                if _all_match(cand_cols[c], gold_idx[j], self.cell_match, values_are_gold=False)
                and (
                    not both_ways
                    or _all_match(gold_cols[j], cand_idx[c], self.cell_match, values_are_gold=True)
                )
            ]
            for j in range(self.arity)
        ]
        yield from _increasing_choices(fits)

    def cell_sorted(self, both_ways: bool) -> Iterator[tuple[int, ...]]:
        """Column sets for the cell-sorted reading, where each candidate cell meets SOME cell of a
        gold row: a usable column holds only values found somewhere in gold; and, when
        `both_ways`, every gold value must be held by some usable column."""
        if not self.cand_rows:
            yield from self._no_rows()
            return
        gold_values = _distinct([cell for row in self.gold_rows for cell in row])
        gold_cells = self._index(gold_values)
        usable = [
            c
            for c in range(self.width)
            if _all_match(
                _column(self.cand_rows, c), gold_cells, self.cell_match, values_are_gold=False
            )
        ]
        if both_ways:
            held = self._index(_distinct([row[c] for row in self.cand_rows for c in usable]))
            if not _all_match(gold_values, held, self.cell_match, values_are_gold=True):
                return
            if len(self.cand_rows) == 1 and len(self.gold_rows) == 1:
                # One row: the chosen cells pair one to one with gold's, so a set exists only if
                # each gold cell can take a DIFFERENT usable column; that pairing is tried first.
                pairing = self._pair_cells(self.gold_rows[0], self.cand_rows[0], usable)
                if pairing is None:
                    return
                first = tuple(sorted(pairing))
                yield first
                yield from (keep for keep in combinations(usable, self.arity) if keep != first)
                return
        yield from combinations(usable, self.arity)

    def _pair_cells(
        self, gold_row: tuple[Any, ...], cand_row: tuple[Any, ...], usable: list[int]
    ) -> list[int] | None:
        def edge(g: Any, c: Any) -> bool:
            self.work.spend()
            try:
                return self.cell_match(c, g)
            except (ArithmeticError, ValueError):
                return True

        edges = [[c for c in usable if edge(g, cand_row[c])] for g in gold_row]
        owner: dict[int, int] = {}
        if not all(_augment(j, edges, owner) for j in range(len(gold_row))):
            return None
        return list(owner)
