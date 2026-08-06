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

Gold may be a SET of accepted results (Spider 2.0-lite publishes several per
question); the candidate passes against any of them, and the single-gold shape
is the one-element case. A per-accepted-result ``condition_cols`` restriction
-- the columns the benchmark's own evaluator scores -- loosens only the
got-facts reading; exact match stays a full-table claim so the strict number
means the same thing on every benchmark.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _GoldVariant:
    """One acceptable gold table, with the benchmark's column restriction."""

    result_set: ResultSet
    row_count: int
    condition_cols: tuple[int, ...]


def _gold_variants(gold_answer: dict[str, Any]) -> list[_GoldVariant]:
    """Every acceptable gold for the item, in one canonical shape.

    A set of ``accepted_results`` -- any of which passes -- is the general
    form of gold; the single ``rows``/``columns`` shape (BIRD's) reads as a
    one-element set. ``condition_cols`` aligns positionally: for each accepted
    result, which of its columns the benchmark's own evaluator scores.
    """
    accepted = gold_answer.get("accepted_results")
    if isinstance(accepted, list) and accepted:
        restrictions = gold_answer.get("condition_cols") or []
        variants: list[_GoldVariant] = []
        for index, entry in enumerate(accepted):
            if not isinstance(entry, dict):
                continue
            rows = _rows_from(entry.get("rows"))
            if rows is None:
                continue
            restriction = restrictions[index] if index < len(restrictions) else []
            variants.append(
                _GoldVariant(
                    result_set=ResultSet(
                        columns=_columns_from(entry.get("columns"), entry.get("rows")),
                        rows=rows,
                    ),
                    row_count=int(entry.get("row_count") or len(rows)),
                    condition_cols=tuple(int(c) for c in (restriction or [])),
                )
            )
        return variants
    rows = _rows_from(gold_answer.get("rows"))
    if rows is None:
        return []
    return [
        _GoldVariant(
            result_set=ResultSet(
                columns=_columns_from(gold_answer.get("columns"), gold_answer.get("rows")),
                rows=rows,
            ),
            row_count=int(gold_answer.get("row_count") or len(rows)),
            condition_cols=(),
        )
    ]


def _project_gold(gold: ResultSet, condition_cols: tuple[int, ...]) -> ResultSet:
    """The benchmark-scored columns of a gold table; empty means all of it.

    Applied to the got-facts reading only: exact match stays a full-table
    claim, so the strict number means the same thing on every benchmark.
    """
    if not condition_cols:
        return gold
    arity = len(gold.rows[0]) if gold.rows else len(gold.columns)
    keep = [i for i in condition_cols if 0 <= i < arity]
    return ResultSet(
        columns=[gold.columns[i] for i in keep if i < len(gold.columns)],
        rows=[tuple(row[i] for i in keep) for row in gold.rows],
    )


class ResultSetMatchGrader:
    name = "result_set_match"
    # v4: gold may be a set of accepted results (pass against any), with
    # per-accepted-result condition_cols honoured on the got-facts reading.
    version = "v4"
    kind = GraderKind.EXECUTION
    # The strict reading decides the outcome; grade() also emits got_facts.
    metric: str | None = "exact_match"

    def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:
        """Apply when the push carries rows and the item's gold is materialized."""
        if result.output_kind != "sql":
            return False
        if _rows_from(result.output.get("rows")) is None:
            return False
        return bool(_gold_variants(item.ground_truth or {}))

    def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:
        """Compare pushed rows against materialized gold rows, any accepted gold."""
        gold_answer = item.ground_truth or {}
        variants = _gold_variants(gold_answer)  # applicable() guarantees at least one

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

        # The candidate passes against ANY accepted gold. Each variant gets the
        # same reading a single gold would; the first variant's diagnosis is
        # the reported one when nothing matches.
        passed = False
        facts = False
        matched_index: int | None = None
        facts_index: int | None = None
        mismatches: list[Mismatch | None] = []
        for index, variant in enumerate(variants):
            gold = variant.result_set
            evidence_complete_v = (
                len(candidate.rows) == candidate_row_count
                and len(gold.rows) == variant.row_count
            )
            mismatch_v: Mismatch | None = None
            if candidate_row_count != variant.row_count:
                passed_v = False
                facts_v = False
                mismatch_v = Mismatch(
                    "row_count",
                    f"candidate returned {candidate_row_count} rows, "
                    f"gold returned {variant.row_count}",
                )
            elif evidence_complete_v:
                passed_v = compare_rows(candidate.rows, gold.rows, order_sensitive, tolerance)
                facts_v = passed_v or got_facts(
                    candidate, _project_gold(gold, variant.condition_cols), tolerance
                )
                if not passed_v:
                    mismatch_v = diagnose(candidate, gold, order_sensitive, tolerance)
            else:
                # A preview: the true counts agree; every visible row must be
                # accounted for in gold. Weaker evidence, and labelled as such.
                passed_v = contains_rows(candidate.rows, gold.rows, tolerance)
                facts_v = passed_v or got_facts_contained(
                    candidate, _project_gold(gold, variant.condition_cols), tolerance
                )
                if not passed_v:
                    mismatch_v = Mismatch(
                        "values",
                        "a pushed row matches no gold row (compared as a preview: "
                        "the push carries fewer rows than it counted)",
                    )
            mismatches.append(mismatch_v)
            if passed_v and matched_index is None:
                matched_index = index
            if facts_v and facts_index is None:
                facts_index = index
            passed = passed or passed_v
            facts = facts or facts_v
            if passed and facts:
                break

        # Evidence in raw reads from the CLOSEST gold: the one that matched
        # exactly, else the one the facts matched (its mismatch names what
        # exact still lacks -- e.g. column order), else the first.
        shown_index = (
            matched_index
            if matched_index is not None
            else facts_index
            if facts_index is not None
            else 0
        )
        shown = variants[shown_index]
        gold = shown.result_set
        gold_row_count = shown.row_count
        evidence_complete = (
            len(candidate.rows) == candidate_row_count and len(gold.rows) == gold_row_count
        )
        mismatch = None if passed else mismatches[shown_index]

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
        if len(variants) > 1:
            raw["accepted_result_count"] = len(variants)
        if matched_index is not None:
            raw["matched_accepted_index"] = matched_index
        if any(v.condition_cols for v in variants):
            raw["condition_cols"] = [list(v.condition_cols) for v in variants]
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
