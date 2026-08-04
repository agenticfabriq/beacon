"""Execution-grounded SQL grader.

Executes candidate and gold SQL in one engine and compares the result sets.
The comparison itself lives in :mod:`beacon_graders.comparison`, shared with
the result-set grader that grades pushed rows without executing anything.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from beacon_graders.comparison import (
    Mismatch,
    ResultSet,
    compare_rows,
    diagnose,
    got_facts,
)
from beacon_graders.tolerance import Tolerance
from beacon_graders.types import GraderKind, Verdict

if TYPE_CHECKING:
    from collections.abc import Callable

    import sqlalchemy as sa
    from beacon_runner.types import EvalItem, ExecutionResult

_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)

# Dialects exposing a server-side per-statement timeout via SET LOCAL.
_STATEMENT_TIMEOUT_DIALECTS = frozenset({"postgresql"})


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
        passed = compare_rows(candidate.rows, gold.rows, order_sensitive, tolerance)
        facts = passed or got_facts(candidate, gold, tolerance)
        mismatch: Mismatch | None = (
            None if passed else diagnose(candidate, gold, order_sensitive, tolerance)
        )
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
                bool_value=facts,
                value=1.0 if facts else 0.0,
                justification=(
                    "Gold's data is present in the candidate (shape-tolerant)."
                    if facts
                    else "Gold's data is not present in the candidate, in any column projection."
                ),
                raw_output={"exact_match": passed},
            ),
        ]

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
