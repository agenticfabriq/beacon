"""Result-set grader: grades pushed rows against gold materialized at import.

The answer-authority path. The runner executed its own SQL in its own engine
and pushed the rows it got; beacon holds the gold answer as rows materialized
once from the reference engine. Grading is a pure comparison -- no execution,
no benchmark database, no dialect. The SQL string rides along as evidence for
the drill-down and is never scored.

Pushed evidence may be a bounded preview (mnemiq reports carry the first 100
rows plus the true count). The true row counts always compare exactly; when
the visible rows are complete the comparison is the full one, and when they
are a preview the reading falls back to multiset containment -- every visible
row must be accounted for in gold -- with ``evidence_truncated`` recorded on
the verdict so the drill-down shows the epistemic status.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from beacon_graders.comparison import (
    Mismatch,
    ResultSet,
    canonicalize_rows,
    compare_rows,
    contains_rows,
    diagnose,
    got_facts,
    got_facts_contained,
)
from beacon_graders.tolerance import Tolerance
from beacon_graders.types import GraderKind, Verdict

if TYPE_CHECKING:
    from beacon_runner.types import EvalItem, ExecutionResult

_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)

# The most rows a push may carry -- BIRD's own cap on gold result sets. More
# is truncated (recorded), never an error: the true count still grades.
MAX_PUSHED_ROWS = 1000


def _rows_from(payload: Any) -> list[tuple[Any, ...]] | None:
    """Parse a pushed or materialized ``rows`` value into canonical row tuples.

    Accepts a list of lists (the wire shape) or a list of dicts (mnemiq's
    report shape, column name -> value; dict order is insertion order).
    """
    if not isinstance(payload, list):
        return None
    rows: list[tuple[Any, ...]] = []
    for entry in payload:
        if isinstance(entry, dict):
            rows.append(tuple(entry.values()))
        elif isinstance(entry, (list, tuple)):
            rows.append(tuple(entry))
        else:
            return None
    return canonicalize_rows(rows)


def _columns_from(payload: Any, rows_payload: Any) -> list[str]:
    if isinstance(payload, list) and all(isinstance(c, str) for c in payload):
        return list(payload)
    if isinstance(rows_payload, list) and rows_payload and isinstance(rows_payload[0], dict):
        return [str(k) for k in rows_payload[0]]
    return []


class ResultSetMatchGrader:
    name = "result_set_match"
    version = "v2"
    kind = GraderKind.EXECUTION
    # The strict reading decides the outcome; grade() also emits got_facts.
    metric: str | None = "exact_match"

    def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:
        """Apply when the push carries rows and the item's gold is materialized."""
        if result.output_kind != "sql":
            return False
        if _rows_from(result.output.get("rows")) is None:
            return False
        gold = item.ground_truth or {}
        return _rows_from(gold.get("rows")) is not None

    def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:
        """Compare pushed rows against materialized gold rows."""
        gold_answer = item.ground_truth or {}
        gold_rows = _rows_from(gold_answer.get("rows")) or []
        gold = ResultSet(
            columns=_columns_from(gold_answer.get("columns"), gold_answer.get("rows")),
            rows=gold_rows,
        )
        gold_row_count = int(gold_answer.get("row_count") or len(gold.rows))

        pushed_rows_payload = result.output.get("rows")
        candidate_rows = _rows_from(pushed_rows_payload) or []
        truncated_at_cap = len(candidate_rows) > MAX_PUSHED_ROWS
        if truncated_at_cap:
            candidate_rows = candidate_rows[:MAX_PUSHED_ROWS]
        candidate = ResultSet(
            columns=_columns_from(result.output.get("columns"), pushed_rows_payload),
            rows=candidate_rows,
        )
        candidate_row_count = int(result.output.get("row_count") or len(candidate.rows))

        gold_sql = gold_answer.get("sql")
        order_sensitive = bool(_ORDER_BY_RE.search(str(gold_sql or "")))
        tolerance = Tolerance.for_item(item)
        if tolerance.row_order_insensitive is not None:
            # Curated gold outranks the ORDER BY heuristic.
            order_sensitive = not tolerance.row_order_insensitive

        evidence_complete = (
            len(candidate.rows) == candidate_row_count and len(gold.rows) == gold_row_count
        )

        mismatch: Mismatch | None = None
        if candidate_row_count != gold_row_count:
            passed = False
            facts = False
            mismatch = Mismatch(
                "row_count",
                f"candidate returned {candidate_row_count} rows, gold returned {gold_row_count}",
            )
        elif evidence_complete:
            passed = compare_rows(candidate.rows, gold.rows, order_sensitive, tolerance)
            facts = passed or got_facts(candidate, gold, order_sensitive, tolerance)
            if not passed:
                mismatch = diagnose(candidate, gold, order_sensitive, tolerance)
        else:
            # A preview: the true counts agree; every visible row must be
            # accounted for in gold. Weaker evidence, and labelled as such.
            passed = contains_rows(candidate.rows, gold.rows, tolerance)
            facts = passed or got_facts_contained(candidate, gold, tolerance)
            if not passed:
                mismatch = Mismatch(
                    "values",
                    "a pushed row matches no gold row (compared as a preview: "
                    "the push carries fewer rows than it counted)",
                )

        raw: dict[str, Any] = {
            "candidate_sql": result.output.get("sql"),
            "gold_sql": gold_sql,
            "order_sensitive": order_sensitive,
            "candidate_row_count": candidate_row_count,
            "gold_row_count": gold_row_count,
            "candidate_columns": candidate.columns,
            "gold_columns": gold.columns,
            "candidate_sample": candidate.sample(),
            "gold_sample": gold.sample(),
            "evidence_complete": evidence_complete,
        }
        if not evidence_complete:
            raw["evidence_truncated"] = True
        if truncated_at_cap:
            raw["pushed_rows_capped_at"] = MAX_PUSHED_ROWS
        if (engine := result.output.get("engine")) is not None:
            raw["engine"] = engine
        if (portable := result.output.get("portable_to_gold_engine")) is not None:
            raw["portable_to_gold_engine"] = portable
        if mismatch is not None:
            raw["mismatch"] = mismatch.as_dict()

        return [
            Verdict(
                grader=self.name,
                grader_version=self.version,
                criterion="correctness",
                bool_value=passed,
                value=1.0 if passed else 0.0,
                justification=(
                    "Candidate result set matches gold."
                    if mismatch is None
                    else f"Mismatch ({mismatch.kind}): {mismatch.detail}"
                ),
                raw_output=raw,
            ),
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
