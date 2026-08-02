"""Execution-grounded SQL grader."""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from beacon_graders.types import GraderKind, Verdict

if TYPE_CHECKING:
    from collections.abc import Callable

    import sqlalchemy as sa
    from beacon_runner.types import EvalItem, ExecutionResult

_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)
# Dialects exposing a server-side per-statement timeout via SET LOCAL.
_STATEMENT_TIMEOUT_DIALECTS = frozenset({"postgresql"})
# Decimal places at which two numeric results are considered the same value.
# Wide enough to absorb DECIMAL-vs-REAL cast differences, tight enough that
# genuinely different answers stay different.
_NUMERIC_COMPARISON_DECIMALS = 6


class ExecutionGroundedSqlGrader:
    name = "execution_grounded_sql"
    version = "v1"
    kind = GraderKind.EXECUTION

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

        passed = self._compare(candidate_rows, gold_rows, order_sensitive)
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
    ) -> bool:
        normalised_candidate = [self._normalise_row(row) for row in candidate]
        normalised_gold = [self._normalise_row(row) for row in gold]
        if order_sensitive:
            return normalised_candidate == normalised_gold
        try:
            return sorted(normalised_candidate) == sorted(normalised_gold)
        except TypeError:
            return Counter(normalised_candidate) == Counter(normalised_gold)

    def _normalise_row(self, row: tuple[Any, ...]) -> tuple[Any, ...]:
        return tuple(self._normalise_value(value) for value in row)

    def _normalise_value(self, value: Any) -> Any:
        """Put numbers on a common footing before comparing.

        Candidate and gold routinely cast the same quantity differently --
        ``CAST(x AS DECIMAL)`` against ``CAST(x AS REAL)`` -- and the driver
        then hands back ``Decimal('0.90490797546012269939')`` for one and
        ``0.904908`` for the other. Compared as Python objects those are
        unequal, so a numerically correct answer scored FAIL. BIRD is full of
        ratio and average questions that hit this.

        Only real numbers are touched: strings, booleans, NULLs, row counts,
        column arity and column order all stay exact.
        """
        if isinstance(value, bool):
            # bool subclasses int, so it would otherwise be rounded into a
            # float. Kept as-is; it still compares equal to 1/0 through
            # Python's own ==, which is the tolerant reading given drivers
            # disagree on how a boolean column comes back.
            return value
        if isinstance(value, Decimal | float):
            return round(float(value), _NUMERIC_COMPARISON_DECIMALS)
        return value

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
