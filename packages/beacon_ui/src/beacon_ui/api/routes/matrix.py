"""The results matrix, and the questions a benchmark scores against.

The matrix is the page the hand-maintained benchmark tracker becomes: one row
per system · version · model · config, aggregated over that configuration's
valid runs. Nothing here re-grades — it reads the outcomes ingestion already
composed, with the register's semantics: ERROR leaves the denominator, DEFER
stays in it, and a rate over nothing gradeable is None.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

import sqlalchemy as sa
from beacon_iam.permissions import Permission
from beacon_storage.models.eval_items import EvalItem
from beacon_storage.models.runs import Result, Run, Verdict
from beacon_storage.models.solutions import Solution
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.suites import SuiteRepo
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.matrix import (
    MatrixOut,
    MatrixRowOut,
    SuiteItemListOut,
    SuiteItemRowOut,
)

router = APIRouter(prefix="/v1", tags=["matrix"])

_GRADED = ("PASS", "FAIL", "DEFER")


def _item_join_clause() -> sa.ColumnElement[bool]:
    """Join a result to the live version of the item it answers."""
    return sa.and_(
        sa.cast(Result.item_id, sa.Uuid) == EvalItem.item_id,
        EvalItem.valid_to.is_(None),
    )


def _rate(part: int, whole: int) -> float | None:
    """A rate over nothing gradeable is unknown, not zero."""
    return part / whole if whole else None


def _per_run_rate(filters: list[sa.ColumnElement[bool]]) -> sa.Subquery:
    """Each valid run's own headline rate, one row per run.

    A row aggregating several runs reports one number, and a reader takes it
    for a quantity. It is a mean over repetitions that disagree -- measured on
    the first real sweep, one arm's three passes read 53.4 / 58.6 / 51.7 and
    the row said 54.3. Repeating a config is how you learn the error bar, so
    the row must be able to SHOW the spread rather than average it away.
    """
    return (
        sa.select(
            Run.id.label("run_id"),
            (
                sa.cast(sa.func.count().filter(Result.outcome == "PASS"), sa.Float)
                / sa.func.nullif(sa.func.count().filter(Result.outcome.in_(_GRADED)), 0)
            ).label("rate"),
        )
        .select_from(Run)
        .join(Result, Result.run_id == Run.id)
        .where(*filters)
        .group_by(Run.id)
        .subquery("per_run_rate")
    )


def _latest_reading(metric: str) -> sa.Subquery:
    """The CURRENT verdict per result for one metric, one row each.

    Grader versions accumulate on a result (history, not garbage), so a bare
    join sees every era -- and an aggregate over it silently means "any
    version true". One row per result: the most recently written reading.
    Newest by id, not by version string -- ids are UUIDv7 (write-ordered),
    while "v10" sorts before "v2" as text.
    """
    verdict = sa.orm.aliased(Verdict)
    return (
        sa.select(
            verdict.result_id,
            verdict.bool_value,
            # The reading's own declared scope. Only got_facts sets it, and
            # only from the version that began declaring it -- NULL means "this
            # verdict predates the disclosure", which is not the same as "the
            # whole table was compared" and must not be counted as it.
            verdict.raw_output["scope"].astext.label("scope"),
        )
        .where(verdict.metric == metric)
        .distinct(verdict.result_id)
        .order_by(verdict.result_id, verdict.id.desc())
        .subquery(f"latest_{metric}")
    )


@router.get(
    "/suites/{suite_id}/results-matrix",
    response_model=MatrixOut,
    summary="Aggregate results, one row per system · version · model · config",
)
@requires(Permission.EVAL_VIEW)
def results_matrix(
    suite_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_VIEW, scope_kind="suite")),
    ],
    session: Annotated[Session, Depends(get_session)],
    difficulty: Annotated[str | None, Query()] = None,
) -> MatrixOut:
    """Group valid runs by configuration identity and aggregate their results."""
    filters: list[sa.ColumnElement[bool]] = [
        Run.suite_id == suite_id,
        Run.invalidated_at.is_(None),
    ]
    engine_expr = sa.func.coalesce(Run.config["engine"].astext, "")
    retrieval_k_expr = Run.config["retrieval_k"].astext
    # Two verdict readings: the strict exact_match beside the tolerant
    # got_facts. EX stays the benchmark-headline number (each benchmark
    # defines its own); these two are beacon's suite-independent claims, one
    # grader across benchmarks -- and each read at its CURRENT version only.
    got_facts_now = _latest_reading("got_facts")
    exact_now = _latest_reading("exact_match")
    per_run = _per_run_rate(filters)

    stmt = (
        sa.select(
            Run.solution_id,
            Solution.solution_id.label("solution_name"),
            Solution.version.label("solution_version"),
            Run.model_id,
            Run.config_label,
            Run.config_digest,
            # A knob that is in the digest but on no screen splits a row and
            # cannot say why. Two retrieval depths under the default label
            # ("single-shot") are two rows, correctly, and identical to look at
            # -- which is how a reader attributes a difference to the wrong
            # thing. Named explicitly rather than dumping the config: that blob
            # can carry a secret reference, and a table is not the place to
            # discover one.
            retrieval_k_expr.label("retrieval_k"),
            engine_expr.label("engine"),
            # The arms pooled into this row, made visible: a sweep run's
            # config_label is empty and its identity lives in sweep_arm, so a
            # blank cell over a pooled average answered nobody's question.
            # More than one distinct arm in the cell is itself information --
            # the row is pooling things a reader may not want pooled.
            sa.func.string_agg(Run.sweep_arm.distinct(), sa.literal(", ")).label("arms"),
            sa.func.count(sa.func.distinct(Run.id)).label("n_runs"),
            # The spread across the runs pooled here. min/max survive the
            # row multiplication this join causes (unlike a sum), so the
            # per-run subquery can ride along with the item-level counts.
            sa.func.min(per_run.c.rate).label("ex_rate_min"),
            sa.func.max(per_run.c.rate).label("ex_rate_max"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(Result.outcome.in_(_GRADED))
            .label("n_graded"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(Result.outcome == "PASS")
            .label("n_pass"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(Result.outcome == "FAIL")
            .label("n_fail"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(Result.outcome == "DEFER")
            .label("n_defer"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(Result.outcome == "ERROR")
            .label("n_errors"),
            # BIRD-comparable numerator: a pass whose SQL the runner verified
            # against the gold's engine. Only meaningful when the runner
            # supplied the flag at all -- see n_portability_flagged.
            sa.func.count(sa.func.distinct(Result.id))
            .filter(
                Result.outcome == "PASS",
                sa.func.coalesce(Result.output["portable_to_gold_engine"].astext, "true")
                != "false",
            )
            .label("n_pass_target_engine"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(Result.output["portable_to_gold_engine"].astext.isnot(None))
            .label("n_portability_flagged"),
            # The tolerant reading of the same execution. The subquery joins
            # are one row per result by construction; DISTINCT stays anyway --
            # the guarantee belongs in the query, not in a comment about
            # today's shape.
            #
            # _GRADED on the numerators too, and for the same reason the
            # denominator has it. A verdict outlives an ERROR composite, so
            # without this a true reading on an excluded result counts into a
            # rate that excluded the result -- 3 over 2 on a PASS/PASS/ERROR
            # row, which the UI renders as "150%".
            sa.func.count(sa.func.distinct(Result.id))
            .filter(Result.outcome.in_(_GRADED), got_facts_now.c.bool_value.is_(True))
            .label("n_got_facts"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(Result.outcome.in_(_GRADED), exact_now.c.bool_value.is_(True))
            .label("n_exact"),
            # How many results the metric was READ on, true or false. The
            # numerator cannot answer that: a system that matched nothing and
            # a metric that never ran both count zero, and only one of them is
            # a measurement. These decide None vs 0.0 below.
            #
            # Restricted to _GRADED, matching the denominator they gate and
            # the numerators above. Without it, a row whose graded results
            # were never scored reports 0.0 on the strength of a verdict
            # attached to a result the rate excludes.
            sa.func.count(sa.func.distinct(Result.id))
            .filter(Result.outcome.in_(_GRADED), got_facts_now.c.bool_value.isnot(None))
            .label("n_got_facts_scored"),
            # The breakdown of that total, three ways and each counted
            # DIRECTLY. `condition_cols` restricts the tolerant reading to the
            # benchmark's scored columns, so on 45 of spider2's 135 items this
            # metric asks about a strict subset of gold -- 20 of them a single
            # column -- and one number spanning both questions cannot be
            # compared across rows (B72). None of these is derived by
            # subtracting the others: that is how a numerator came to count
            # what its denominator had thrown out (B68).
            sa.func.count(sa.func.distinct(Result.id))
            .filter(
                Result.outcome.in_(_GRADED),
                got_facts_now.c.bool_value.isnot(None),
                got_facts_now.c.scope == "subset",
            )
            .label("n_got_facts_subset_scored"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(
                Result.outcome.in_(_GRADED),
                got_facts_now.c.bool_value.isnot(None),
                got_facts_now.c.scope == "full",
            )
            .label("n_got_facts_full_scored"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(
                Result.outcome.in_(_GRADED),
                got_facts_now.c.bool_value.isnot(None),
                got_facts_now.c.scope.is_(None),
            )
            .label("n_got_facts_scope_unknown"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(Result.outcome.in_(_GRADED), exact_now.c.bool_value.isnot(None))
            .label("n_exact_scored"),
            sa.func.percentile_cont(0.5)
            .within_group(Result.tokens_input + Result.tokens_output)
            .label("median_tokens"),
            sa.func.percentile_cont(0.5).within_group(Result.runtime_ms).label("median_runtime"),
        )
        .join(Result, Result.run_id == Run.id)
        .join(Solution, Solution.id == Run.solution_id)
        .join(got_facts_now, got_facts_now.c.result_id == Result.id, isouter=True)
        .join(exact_now, exact_now.c.result_id == Result.id, isouter=True)
        .join(per_run, per_run.c.run_id == Run.id, isouter=True)
        .where(*filters)
        .group_by(
            Run.solution_id,
            Solution.solution_id,
            Solution.version,
            Run.model_id,
            Run.config_label,
            Run.config_digest,
            retrieval_k_expr,
            engine_expr,
        )
    )
    if difficulty is not None:
        stmt = stmt.join(EvalItem, _item_join_clause()).where(
            EvalItem.item_metadata["difficulty"].astext == difficulty
        )

    rows = []
    for record in session.execute(stmt).all():
        graded = int(record.n_graded)
        rows.append(
            MatrixRowOut(
                solution_id=record.solution_id,
                solution_name=record.solution_name,
                solution_version=record.solution_version,
                model_id=record.model_id,
                config_label=record.config_label,
                config_digest=record.config_digest,
                retrieval_k=(
                    int(record.retrieval_k) if str(record.retrieval_k or "").isdigit() else None
                ),
                arms=record.arms,
                # Only meaningful across repetitions; one run has no spread.
                ex_rate_min=(
                    float(record.ex_rate_min)
                    if int(record.n_runs) > 1 and record.ex_rate_min is not None
                    else None
                ),
                ex_rate_max=(
                    float(record.ex_rate_max)
                    if int(record.n_runs) > 1 and record.ex_rate_max is not None
                    else None
                ),
                engine=str(record.engine) or None,
                n_runs=int(record.n_runs),
                n_graded=graded,
                n_errors=int(record.n_errors),
                ex_rate=_rate(int(record.n_pass), graded),
                ex_target_engine_rate=(
                    _rate(int(record.n_pass_target_engine), graded)
                    if int(record.n_portability_flagged)
                    else None
                ),
                # Gated on whether the metric was read, never on whether it
                # was ever true. Guarding with the numerator made a row that
                # was scored and matched nothing report None -- "the grader
                # never ran" -- and the arms most likely to hit it are the
                # weak ones a reader is trying to tell apart.
                exact_rate=(
                    _rate(int(record.n_exact), graded) if int(record.n_exact_scored) else None
                ),
                got_facts_rate=(
                    _rate(int(record.n_got_facts), graded)
                    if int(record.n_got_facts_scored)
                    else None
                ),
                n_got_facts_scored=int(record.n_got_facts_scored),
                n_got_facts_subset_scored=int(record.n_got_facts_subset_scored),
                n_got_facts_full_scored=int(record.n_got_facts_full_scored),
                n_got_facts_scope_unknown=int(record.n_got_facts_scope_unknown),
                defer_rate=_rate(int(record.n_defer), graded),
                wrong_rate=_rate(int(record.n_fail), graded),
                median_tokens=float(record.median_tokens)
                if record.median_tokens is not None
                else None,
                median_runtime_ms=float(record.median_runtime)
                if record.median_runtime is not None
                else None,
            )
        )
    rows.sort(key=lambda row: (row.ex_rate is None, -(row.ex_rate or 0.0)))

    # Facet counts over the unfiltered selection, so a filtered view still
    # shows what it is a slice of.
    #
    # DISTINCT items, not result rows. A bare count here counted one row per
    # graded attempt, so the facet read runs x items: a 135-question suite
    # showed "all 675" once five runs existed, and grew whenever anyone added
    # a run. The chip names the slice of the BENCHMARK the rates are over --
    # how many attempts backs each rate is n_graded's job, per row.
    level = sa.func.coalesce(EvalItem.item_metadata["difficulty"].astext, "unknown")
    facet_stmt = (
        sa.select(level, sa.func.count(sa.distinct(Result.item_id)))
        .select_from(Run)
        .join(Result, Result.run_id == Run.id)
        .join(EvalItem, _item_join_clause(), isouter=True)
        .where(*filters)
        .group_by(level)
    )
    difficulty_counts = {str(key): int(count) for key, count in session.execute(facet_stmt).all()}
    return MatrixOut(rows=rows, difficulty_counts=difficulty_counts)


@router.get(
    "/suites/{suite_id}/items",
    response_model=SuiteItemListOut,
    summary="The questions a benchmark scores against",
)
@requires(Permission.EVAL_VIEW)
def suite_items(
    suite_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_VIEW, scope_kind="suite")),
    ],
    session: Annotated[Session, Depends(get_session)],
    difficulty: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SuiteItemListOut:
    """List the suite's active items with difficulty and source facets.

    Items are matched by suite *name*: that is the key grading uses, and it is
    what makes the ten-item demo slice and the full corpus the same benchmark.
    Visibility is the suite's own team plus shared items.
    """
    suite = SuiteRepo(session).get(suite_id)
    assert suite is not None  # the permission dependency 404s first

    base = sa.select(EvalItem).where(
        EvalItem.valid_to.is_(None),
        EvalItem.suite == suite.name,
        sa.or_(EvalItem.team_id == suite.team_id, EvalItem.team_id.is_(None)),
    )
    items = list(session.scalars(base.order_by(EvalItem.question_hash, EvalItem.item_id)))

    difficulty_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for item in items:
        level = str(item.item_metadata.get("difficulty") or "unknown")
        difficulty_counts[level] = difficulty_counts.get(level, 0) + 1
        origin = str(item.item_metadata.get("source") or "unknown")
        source_counts[origin] = source_counts.get(origin, 0) + 1

    selected = [
        item
        for item in items
        if (difficulty is None or str(item.item_metadata.get("difficulty")) == difficulty)
        and (source is None or str(item.item_metadata.get("source")) == source)
    ]
    page = selected[offset : offset + limit]

    def _meta_str(item: EvalItem, key: str) -> str | None:
        value = item.item_metadata.get(key)
        return str(value) if value is not None else None

    return SuiteItemListOut(
        items=[
            SuiteItemRowOut(
                item_id=item.item_id,
                question=str((item.item_input or {}).get("question") or ""),
                difficulty=_meta_str(item, "difficulty"),
                database=(
                    str((item.item_input or {}).get("db_id"))
                    if (item.item_input or {}).get("db_id") is not None
                    else None
                ),
                source=_meta_str(item, "source"),
                tolerance=(
                    dict(item.item_metadata["tolerance"])
                    if isinstance(item.item_metadata.get("tolerance"), dict)
                    else None
                ),
                has_gold_sql=bool((item.gold_answer or {}).get("sql")),
                gold_sql=(
                    str((item.gold_answer or {}).get("sql"))
                    if (item.gold_answer or {}).get("sql")
                    else None
                ),
            )
            for item in page
        ],
        total=len(selected),
        difficulty_counts=difficulty_counts,
        source_counts=source_counts,
    )
