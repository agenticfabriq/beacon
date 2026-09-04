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

# One policy, one home: the cap lives at the transport boundary in
# beacon_runner, where an SUT can also reach it without inverting the
# dependency graph. Re-exported here because this is where graders and
# existing callers look for it.
from beacon_runner.transport import MAX_PUSHED_ROWS as MAX_PUSHED_ROWS  # noqa: E402, PLC0414


def rows_from(payload: Any, columns: list[str] | None = None) -> list[tuple[Any, ...]] | None:
    """Parse a pushed or materialized ``rows`` value into canonical row tuples.

    Public because the ingest gate must ask THIS question about a pushed
    `rows` payload rather than a lookalike. ``isinstance(rows, list)`` is the
    natural-looking check and is WIDER than this one: `[1, 2, 3]` is a list,
    so it clears the gate, and then ``applicable`` returns False here, no
    grader emits anything, and the composer falls through to ERROR -- the
    blind grading the gate exists to prevent, wearing an instrument-failure
    label. A gate must ask the grader's own question; see ``gold_variants``
    for the same lesson on the gold side.

    Accepts a list of lists (the wire shape) or a list of dicts (mnemiq's
    report shape, column name -> value). Dict rows are ordered by ``columns``
    when given: JSONB canonicalizes object keys, so a dict read back from
    storage has LOST its wire order, and only an ordered columns array can
    restore it. Insertion order is trusted only when no columns are supplied
    (an in-flight dict, never stored). This is load-bearing for exact match,
    where column order is part of the claim.
    """
    if not isinstance(payload, list):
        return None
    rows: list[tuple[Any, ...]] = []
    for entry in payload:
        if isinstance(entry, dict):
            if columns:
                rows.append(tuple(entry.get(c) for c in columns))
            else:
                rows.append(tuple(entry.values()))
        elif isinstance(entry, (list, tuple)):
            rows.append(tuple(entry))
        else:
            return None
    return canonicalize_rows(rows)


def explicit_columns(payload: Any) -> list[str] | None:
    """An ordered columns declaration, if the record carries a USABLE one.

    Public because callers outside the grader must be able to ask the grader's
    own question. A truthiness test on ``columns`` is the natural-looking
    substitute and it is WIDER: ``[0, 1]`` and ``[{"name": "a"}]`` are truthy
    and answer None here, so a gate written that way clears while ``rows_from``
    falls back to dict insertion order -- the scrambled-column path the gate
    exists to prevent. Same lesson as ``rows_from`` and ``gold_variants``.
    """
    if isinstance(payload, list) and payload and all(isinstance(c, str) for c in payload):
        return list(payload)
    return None


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


def gold_variants(gold_answer: dict[str, Any]) -> list[_GoldVariant]:
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
            rows = rows_from(entry.get("rows"), explicit_columns(entry.get("columns")))
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
    rows = rows_from(gold_answer.get("rows"), explicit_columns(gold_answer.get("columns")))
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
    # DISTINCT, and this matters for the reading rather than the tidiness.
    # Duplicating a repeated index made the projected gold arity 2 for
    # `condition_cols [0, 0]`, so a candidate returning that one scored column
    # ONCE tripped `gold_arity > candidate_arity` and got "Gold's data is not
    # present in the candidate, in any column projection" -- a false statement
    # about a candidate that carried the scored data. It also graded stricter
    # than the benchmark: upstream's `t_gold_list` holds the duplicate twice
    # and its match loop consumes nothing, so ONE prediction column satisfies
    # both. Deduping agrees with upstream and removes the false negative.
    keep = sorted({i for i in condition_cols if 0 <= i < arity})
    return ResultSet(
        columns=[gold.columns[i] for i in keep if i < len(gold.columns)],
        rows=[tuple(row[i] for i in keep) for row in gold.rows],
    )


def _distinct_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """Collapse duplicate rows, keeping first occurrence order."""
    seen: set[str] = set()
    out: list[tuple[Any, ...]] = []
    for row in rows:
        key = repr(row)
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def _scored_columns(variant: _GoldVariant) -> list[str] | None:
    """The gold column NAMES this variant's restriction scores, or None if all.

    Names, not indices: a reader of one verdict should not have to fetch the
    gold to learn that "the facts are present" meant one label column.
    """
    if not variant.condition_cols:
        return None
    arity = (
        len(variant.result_set.rows[0])
        if variant.result_set.rows
        else len(variant.result_set.columns)
    )
    # DISTINCT indices, because coverage is a set question. `condition_cols
    # [0, 0]` on a 2-column gold has len 2 and covers ONE column: counting the
    # list read that as full coverage and disclosed nothing, while
    # `_project_gold` still compared column 0 alone (twice). The disclosure
    # would have failed open on exactly the input it exists to describe.
    # Latent -- 0 of the restricted items in the corpus repeat an index.
    kept = sorted({i for i in variant.condition_cols if 0 <= i < arity})
    if len(kept) >= arity:
        return None
    return [
        variant.result_set.columns[i] if i < len(variant.result_set.columns) else f"col{i}"
        for i in kept
    ]


def _facts_justification(
    facts: bool,
    variants: list[_GoldVariant],
    facts_index: int | None,
    *,
    via_projection: bool,
) -> str:
    """Say WHICH columns the tolerant reading compared when it compared a subset.

    `condition_cols` restricts got-facts to the benchmark's scored columns, so
    on 45 of spider2's 135 items this metric asks about a strict subset of gold
    -- 20 of them a single column. Reported as a bare boolean it reads as "the
    facts are there" either way, and `local300` is why that matters: the
    candidate returned a running total (2120567.0 against gold 356618) and
    passed because only the MONTH column is scored. The scope was already
    recorded, but on the EXACT-MATCH verdict, whose reading it does not affect.
    """
    if not facts:
        return "Gold's data is not present in the candidate, in any column projection."
    # `facts_v = passed_v or got_facts(...)`, so facts can come from the FULL
    # table matching exactly. Narrating "compared on the benchmark-scored
    # columns only" there is false and is the mirror of the over-claim this
    # disclosure exists to fix: the exact reading compared every column.
    scored = (
        _scored_columns(variants[facts_index])
        if via_projection and facts_index is not None
        else None
    )
    if scored is None:
        return "Gold's data is present in the candidate (shape-tolerant)."
    return (
        "Gold's data is present in the candidate (shape-tolerant), compared on the "
        f"benchmark-scored columns only: {', '.join(scored)}. Other gold columns were "
        "not compared, so this is not a claim about them."
    )


def _facts_raw(
    passed: bool,
    variants: list[_GoldVariant],
    facts_index: int | None,
    matched_index: int | None,
    *,
    via_projection: bool,
) -> dict[str, object]:
    """The got-facts verdict's own scope, so one row is self-describing.

    `via_projection` gates the scope for the same reason the justification does:
    when the full table matched exactly, no restriction was applied and
    recording one would misdescribe the reading.
    """
    raw: dict[str, object] = {"exact_match": passed}
    # A POSITIVE scope marker, always, and this is the load-bearing part for
    # aggregating this metric. Recording `scored_columns` only when the reading
    # was restricted leaves "compared every column" and "graded before this
    # was recorded at all" both looking like an absent key -- so an aggregate
    # counting subset-scored verdicts would report 0 for every historical row
    # and read as "nothing is subset-scored", which is an absence rendered as a
    # measurement (B65/B67). With `scope` always present, its ABSENCE means
    # exactly one thing: this verdict predates the disclosure.
    # Derived from what the reading ASKED, not from which variant answered it.
    # An earlier version read the carrying variant, so a restricted item that
    # FAILED got-facts was stamped "full": `facts_via_projection` is false
    # whenever facts is false, so there was no carrier to read. The subset
    # count could then only ever contain restricted items the model PASSED --
    # a composition moving with the pass rate it exists to qualify, which is
    # worse than no breakdown. Measured before the fix: gold `condition_cols
    # [[0]]` with a candidate wrong on the scored column gave
    # `bool_value=False, scope="full"`.
    #
    # An exact full-table match is the one case where nothing was restricted:
    # got-facts is true by `passed_v` without any projection being applied.
    if passed:
        raw["scope"] = "full"
    else:
        restricted = any(_scored_columns(variant) is not None for variant in variants)
        raw["scope"] = "subset" if restricted else "full"
    # The index that CARRIED this verdict. When the exact reading did, that is
    # `matched_index`, not the first variant to facts-match by projection --
    # reporting the latter had the got-facts row name accepted result 0 while
    # the exact row named 1, for one result, with nothing explaining why. And
    # suppressing `scored_columns` (correctly) removed the only marker that
    # had distinguished the two readings, so the mislabel was all a reader saw.
    carrier = matched_index if passed and matched_index is not None else facts_index
    if carrier is not None:
        raw["matched_accepted_index"] = carrier
        scored = _scored_columns(variants[carrier]) if via_projection else None
        if scored is not None:
            # Already "subset" from the reading above; the COLUMNS are the
            # extra a passing verdict can name, because it knows which
            # accepted result it matched.
            raw["scope"] = "subset"
            raw["scored_columns"] = scored
            raw["condition_cols"] = list(variants[carrier].condition_cols)
    return raw


class ResultSetMatchGrader:
    name = "result_set_match"
    # v4: gold may be a set of accepted results (pass against any), with
    # per-accepted-result condition_cols honoured on the got-facts reading.
    # v5: duplicate_rows_insignificant honoured -- BIRD's published set()
    # comparison, declared per item, collapsing duplicates in both metrics.
    # v6: a gold projected to ZERO columns is no longer got-facts (it matched
    # empty tuples row-for-row and passed anything with the right row count),
    # and a subset-scored got-facts verdict now names the columns it compared.
    # The first changes the emitted bool on a reachable input, so two verdicts
    # stamped v5 could otherwise disagree on the same rows.
    # v7: `_project_gold` dedupes `condition_cols`, which changes the bool on
    # the same terms -- `[0, 0]` on a 2-column gold answered False for a
    # candidate carrying that one column once, and now answers True. Bumped
    # because v6 was stamped one commit before the dedupe landed, so the rule
    # in the v6 note applies to v6 itself. `scripts/regrade_suite.py` treats a
    # verdict at the current version as `current` and skips it, so a stale
    # v6 row would never be refreshed and never say so. Nothing stored is
    # affected: v6 never reached the deployed image, which still serves v5.
    # v8: the got-facts verdict declares its SCOPE, which the matrix aggregate
    # reads to say what the rate is a rate over (B72). Bumped even though no
    # bool moves, because `scripts/regrade_suite.py` counts a result already
    # carrying a verdict at this version as `current` and SKIPS it -- so any v7
    # verdict written since 361a855 would keep an unrecorded scope for ever and
    # be reported as "already at this version". A raw_output key the aggregate
    # depends on is part of the reading, not decoration.
    version = "v8"
    kind = GraderKind.EXECUTION
    # The strict reading decides the outcome BY DEFAULT; grade() also emits
    # got_facts.
    metric: str | None = "exact_match"
    # Every metric this grader emits, which is not the same question as which
    # one decides. `metric` alone was read as the set of available metrics, so
    # `primary_metric="got_facts"` is refused with "no grader declaring it" --
    # and that is the reading spider2_lite_local_v1 declares as its headline.
    # Declaring one of two readings as "the" metric says which is default, not
    # which exist.
    #
    # This PERMITS a caller to select the tolerant reading; it does not make
    # anything select it. The ingest path still composes under the default
    # until it is taught to read the suite's declaration, so B74 stays open at
    # this revision.
    emits: tuple[str, ...] = ("exact_match", "got_facts")

    def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:
        """Apply when the push carries rows and the item's gold is materialized."""
        if result.output_kind != "sql":
            return False
        if rows_from(result.output.get("rows")) is None:
            return False
        return bool(gold_variants(item.ground_truth or {}))

    def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:
        """Compare pushed rows against materialized gold rows, any accepted gold."""
        gold_answer = item.ground_truth or {}
        variants = gold_variants(gold_answer)  # applicable() guarantees at least one

        pushed_rows_payload = result.output.get("rows")
        pushed_columns = explicit_columns(result.output.get("columns"))
        candidate_rows = rows_from(pushed_rows_payload, pushed_columns) or []
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

        # BIRD's published set() rule, declared per item: duplicates collapse
        # before counts and comparison, in both metrics. The declared true
        # count survives the collapse only when the push was complete -- a
        # preview's distinct count is unknowable and stays as declared.
        dedupe = tolerance.duplicate_rows_insignificant is True
        candidate_was_complete = len(candidate.rows) == candidate_row_count
        if dedupe:
            candidate = ResultSet(columns=candidate.columns, rows=_distinct_rows(candidate.rows))
            if candidate_was_complete:
                candidate_row_count = len(candidate.rows)
            # else: the declared count is a raw count and the distinct count is
            # unknowable from a preview -- the count gate below must not compare
            # raw against distinct, so it stands down and containment decides.

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
            gold_count = variant.row_count
            if dedupe:
                gold_was_complete = len(gold.rows) == gold_count
                gold = ResultSet(columns=gold.columns, rows=_distinct_rows(gold.rows))
                if gold_was_complete:
                    gold_count = len(gold.rows)
            evidence_complete_v = (
                len(candidate.rows) == candidate_row_count and len(gold.rows) == gold_count
            )
            mismatch_v: Mismatch | None = None
            if candidate_row_count != gold_count and not (dedupe and not candidate_was_complete):
                passed_v = False
                facts_v = False
                mismatch_v = Mismatch(
                    "row_count",
                    f"candidate returned {candidate_row_count} rows, "
                    f"gold returned {gold_count}"
                    + (" (distinct, duplicates insignificant)" if dedupe else ""),
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

        # A restriction is worth disclosing only when the tolerant reading is
        # what carried the verdict. If ANY accepted result matched the full
        # table, got-facts is true by exact match and no restriction applied.
        facts_via_projection = facts and not passed

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
        if dedupe:
            raw["duplicate_rows_insignificant"] = True
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
                justification=_facts_justification(
                    facts, variants, facts_index, via_projection=facts_via_projection
                ),
                raw_output=_facts_raw(
                    passed,
                    variants,
                    facts_index,
                    matched_index,
                    via_projection=facts_via_projection,
                ),
            ),
        ]
