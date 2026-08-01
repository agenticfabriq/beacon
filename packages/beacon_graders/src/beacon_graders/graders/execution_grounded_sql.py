"""Execution-grounded SQL grader."""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Any

from beacon_graders.types import GraderKind, Verdict

if TYPE_CHECKING:
    from collections.abc import Callable

    import sqlalchemy as sa
    from beacon_runner.types import EvalItem, ExecutionResult

_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)


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
        with engine.connect() as connection:
            cursor = connection.exec_driver_sql(sql)
            return [tuple(row) for row in cursor.fetchall()]

    def _compare(
        self,
        candidate: list[tuple[Any, ...]],
        gold: list[tuple[Any, ...]],
        order_sensitive: bool,
    ) -> bool:
        if order_sensitive:
            return candidate == gold
        try:
            return sorted(candidate) == sorted(gold)
        except TypeError:
            return Counter(candidate) == Counter(gold)

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
