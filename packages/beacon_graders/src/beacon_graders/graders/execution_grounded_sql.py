"""Execution-grounded SQL grader."""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal
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


class ExecutionGroundedSqlGrader:
    name = "execution_grounded_sql"
    version = "v1"
    kind = GraderKind.EXECUTION
    metric: str | None = None

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
            candidate_rows = self._exec(engine, str(candidate_sql))
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
            gold_rows = self._exec(engine, str(gold_sql))
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
        passed = self._compare(candidate_rows, gold_rows, order_sensitive, tolerance)
        return [
            self._verdict(
                passed=passed,
                justification=(
                    "Candidate result set matches gold."
                    if passed
                    else f"Mismatch: candidate has {len(candidate_rows)} rows, "
                    f"gold has {len(gold_rows)}."
                ),
                raw={
                    "candidate_sql": candidate_sql,
                    "gold_sql": gold_sql,
                    "order_sensitive": order_sensitive,
                    "candidate_row_count": len(candidate_rows),
                    "gold_row_count": len(gold_rows),
                },
            )
        ]

    def _exec(self, engine: sa.Engine, sql: str) -> list[tuple[Any, ...]]:
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
            return [tuple(row) for row in cursor.fetchall()]

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
