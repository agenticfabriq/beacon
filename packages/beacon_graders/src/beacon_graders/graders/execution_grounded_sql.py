"""Execution-grounded SQL grader."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from typing import TYPE_CHECKING, Any

from beacon_graders.tolerance import Tolerance
from beacon_graders.types import GraderKind, Verdict

if TYPE_CHECKING:
    from collections.abc import Callable

    import sqlalchemy as sa
    from beacon_runner.types import EvalItem, ExecutionResult

_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)


def _is_number(value: Any) -> bool:
    """Return whether a cell is a real number. Bools are not: True is not 1."""
    return isinstance(value, Decimal | float | int) and not isinstance(value, bool)


def _sort_key(row: tuple[Any, ...]) -> tuple[str, ...]:
    """Order rows for order-insensitive comparison without comparing mixed types."""
    return tuple(repr(v) for v in row)


# Dialects exposing a server-side per-statement timeout via SET LOCAL.
_STATEMENT_TIMEOUT_DIALECTS = frozenset({"postgresql"})

# How many rows of each side to keep on the verdict. Enough to show a reader
# what the two answers looked like, bounded so a 7,806-row gold cannot bloat
# every verdict row that references it.
SAMPLE_ROWS = 5


def _jsonable(value: Any) -> Any:
    """Coerce a database cell to something JSONB can hold, losing nothing visible."""
    if value is None or isinstance(value, bool | float | int | str):
        return value
    return str(value)


@dataclass(frozen=True)
class ResultSet:
    """The rows a query returned, and the names of their columns."""

    columns: list[str]
    rows: list[tuple[Any, ...]]

    def sample(self, limit: int = SAMPLE_ROWS) -> list[list[Any]]:
        """Return the first ``limit`` rows, JSON-safe."""
        return [[_jsonable(cell) for cell in row] for row in self.rows[:limit]]


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


# Trying every way to project a wide candidate onto the gold's arity is
# combinatorial; past this many projections the tolerant reading gives up and
# says so, rather than stalling the grader on a pathological SELECT *.
MAX_PROJECTIONS = 100


class ExecutionGroundedSqlGrader:
    name = "execution_grounded_sql"
    version = "v1"
    kind = GraderKind.EXECUTION
    # The strict reading decides the outcome. grade() also emits a second,
    # tolerant verdict under "got_facts"; the composer stamps this metric only
    # onto verdicts that carry none, so the two stay distinct.
    metric: str | None = "exact_match"

    def __init__(
        self,
        *,
        engine_factory: Callable[[EvalItem], sa.Engine],
        statement_timeout_seconds: int = 60,
    ) -> None:
        self.engine_factory = engine_factory
        # Applied per statement in _exec. Set to 0 to disable the bound.
        self.statement_timeout_seconds = statement_timeout_seconds

    def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:
        """Apply when ``result`` is SQL and the item ships a gold ``sql`` query."""
        if result.output_kind != "sql":
            return False
        return "sql" in (item.ground_truth or {})

    def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:
        """Execute candidate and gold SQL and compare result sets order-aware."""
        candidate_sql = result.output.get("sql")
        gold_sql = (item.ground_truth or {}).get("sql")

        if not candidate_sql:
            return [
                self._verdict(
                    passed=False,
                    justification="No SQL produced by SUT.",
                    raw={"candidate_sql": None, "gold_sql": gold_sql},
                )
            ]
        if not gold_sql:
            return [
                self._verdict(
                    passed=False,
                    justification="No gold SQL in EvalItem.ground_truth.",
                    raw={"candidate_sql": candidate_sql, "gold_sql": None},
                )
            ]

        engine = self.engine_factory(item)
        order_sensitive = bool(_ORDER_BY_RE.search(str(gold_sql)))

        try:
            candidate = self._exec(engine, str(candidate_sql))
        except Exception as exc:
            return [
                self._verdict(
                    passed=False,
                    justification=f"Candidate SQL failed: {exc!r}",
                    raw={
                        "candidate_sql": candidate_sql,
                        "gold_sql": gold_sql,
                        "error": str(exc),
                    },
                )
            ]
        try:
            gold = self._exec(engine, str(gold_sql))
        except Exception as exc:
            return [
                self._verdict(
                    passed=False,
                    justification=f"Gold SQL failed: {exc!r}",
                    raw={
                        "candidate_sql": candidate_sql,
                        "gold_sql": gold_sql,
                        "error": str(exc),
                    },
                )
            ]

        tolerance = Tolerance.for_item(item)
        if tolerance.row_order_insensitive is not None:
            # Curated gold outranks the ORDER BY heuristic.
            order_sensitive = not tolerance.row_order_insensitive
        passed = self._compare(candidate.rows, gold.rows, order_sensitive, tolerance)
        got_facts = passed or self._got_facts(candidate, gold, order_sensitive, tolerance)
        mismatch = None if passed else self._diagnose(candidate, gold, order_sensitive, tolerance)
        raw: dict[str, Any] = {
            "candidate_sql": candidate_sql,
            "gold_sql": gold_sql,
            "order_sensitive": order_sensitive,
            "candidate_row_count": len(candidate.rows),
            "gold_row_count": len(gold.rows),
            # Kept so a reader can see both answers without re-executing the
            # queries -- which would answer from today's database, not the one
            # the verdict was computed against.
            "candidate_columns": candidate.columns,
            "gold_columns": gold.columns,
            "candidate_sample": candidate.sample(),
            "gold_sample": gold.sample(),
        }
        if mismatch is not None:
            raw["mismatch"] = mismatch.as_dict()
        return [
            self._verdict(
                passed=passed,
                justification=(
                    "Candidate result set matches gold."
                    if mismatch is None
                    else f"Mismatch ({mismatch.kind}): {mismatch.detail}"
                ),
                raw=raw,
            ),
            # The second reading of the same execution: right data, tolerant
            # shape. Reported beside exact match, never instead of it -- the
            # explicit metric keeps the composer's stamp off it, and emission
            # order keeps the strict verdict the one that decides the outcome.
            Verdict(
                grader=self.name,
                grader_version=self.version,
                metric="got_facts",
                criterion="correctness",
                bool_value=got_facts,
                value=1.0 if got_facts else 0.0,
                justification=(
                    "Gold's data is present in the candidate (shape-tolerant)."
                    if got_facts
                    else "Gold's data is not present in the candidate, in any column projection."
                ),
                raw_output={"exact_match": passed},
            ),
        ]

    def _got_facts(
        self,
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

        def sort_cells(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
            return [tuple(sorted(row, key=repr)) for row in rows]

        gold_sorted = sort_cells(gold.rows)
        for index, keep in enumerate(combinations(range(candidate_arity), gold_arity)):
            if index >= MAX_PROJECTIONS:
                return False
            projected = [tuple(row[i] for i in keep) for row in candidate.rows]
            if self._compare(projected, gold.rows, order_sensitive, tolerance):
                return True
            if self._compare(sort_cells(projected), gold_sorted, order_sensitive, tolerance):
                return True
        return False

    def _diagnose(
        self,
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
                left, right = sorted(left, key=_sort_key), sorted(right, key=_sort_key)
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
                if not self._values_match(cell, gold_cell, tolerance):
                    name = candidate.columns[column] if column < len(candidate.columns) else column
                    return Mismatch(
                        "values",
                        f"row {index}, column {name!r}: candidate {cell!r} != gold {gold_cell!r}",
                    )
        return Mismatch(
            "values", "rows differ under the comparison but no differing cell was found"
        )

    def _exec(self, engine: sa.Engine, sql: str) -> ResultSet:
        # Drivers with %-based paramstyles (e.g. psycopg) parse "%" in the
        # statement as a placeholder even without bound parameters, so a
        # literal "%" (LIKE patterns in gold SQL) must be doubled for them.
        if engine.dialect.paramstyle in ("pyformat", "format"):
            sql = sql.replace("%", "%%")
        with engine.connect() as connection, connection.begin():
            # The SQL here is model-generated and arbitrary. Without a bound, a
            # pathological candidate (a cross join, say) holds this worker and
            # pins its connection until the harness per-item timeout, which on a
            # shared benchmark engine serialises the whole run.
            self._apply_statement_timeout(connection)
            cursor = connection.exec_driver_sql(sql)
            # RMKeyView, not a mapping: iterating the cursor itself yields rows.
            columns = [str(key) for key in cursor.keys()]  # noqa: SIM118
            return ResultSet(columns=columns, rows=[tuple(row) for row in cursor.fetchall()])

    def _apply_statement_timeout(self, connection: sa.Connection) -> None:
        """Bound the next statement on this connection where the dialect allows it.

        ``SET LOCAL`` is scoped to the surrounding transaction, so the bound is
        undone on rollback and never leaks to the next borrower of a pooled
        connection. Dialects with no server-side statement timeout (SQLite) are
        left alone -- see ``supports_statement_timeout``.
        """
        if self.statement_timeout_seconds <= 0:
            return
        if not self.supports_statement_timeout(connection.dialect.name):
            return
        timeout_ms = int(self.statement_timeout_seconds * 1000)
        connection.exec_driver_sql(f"SET LOCAL statement_timeout = {timeout_ms}")

    @staticmethod
    def supports_statement_timeout(dialect_name: str) -> bool:
        """Return whether ``dialect_name`` honours a server-side statement timeout."""
        return dialect_name in _STATEMENT_TIMEOUT_DIALECTS

    def _compare(
        self,
        candidate: list[tuple[Any, ...]],
        gold: list[tuple[Any, ...]],
        order_sensitive: bool,
        tolerance: Tolerance | None = None,
    ) -> bool:
        tol = tolerance or Tolerance()
        if len(candidate) != len(gold):
            return False
        if order_sensitive:
            return self._rows_match(candidate, gold, tol)
        try:
            ordered_candidate = sorted(candidate, key=_sort_key)
            ordered_gold = sorted(gold, key=_sort_key)
        except TypeError:
            return Counter(map(repr, candidate)) == Counter(map(repr, gold))
        return self._rows_match(ordered_candidate, ordered_gold, tol)

    def _rows_match(
        self,
        candidate: list[tuple[Any, ...]],
        gold: list[tuple[Any, ...]],
        tolerance: Tolerance,
    ) -> bool:
        return all(self._row_matches(c, g, tolerance) for c, g in zip(candidate, gold, strict=True))

    def _row_matches(
        self,
        candidate: tuple[Any, ...],
        gold: tuple[Any, ...],
        tolerance: Tolerance,
    ) -> bool:
        if len(candidate) != len(gold):
            return False
        return all(
            self._values_match(c, g, tolerance) for c, g in zip(candidate, gold, strict=True)
        )

    def _values_match(self, candidate: Any, gold: Any, tolerance: Tolerance) -> bool:
        """Compare one cell, numbers within tolerance and everything else exactly."""
        if _is_number(candidate) and _is_number(gold):
            return tolerance.numbers_match(float(candidate), float(gold))
        return bool(candidate == gold)

    def _verdict(self, *, passed: bool, justification: str, raw: dict[str, Any]) -> Verdict:
        return Verdict(
            grader=self.name,
            grader_version=self.version,
            criterion="correctness",
            bool_value=passed,
            value=1.0 if passed else 0.0,
            justification=justification,
            raw_output=raw,
        )
