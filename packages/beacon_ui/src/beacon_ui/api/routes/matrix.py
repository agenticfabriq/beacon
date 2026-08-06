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
    # Second verdict reading, aliased: the strict exact_match beside the
    # tolerant got_facts. EX stays the benchmark-headline number (each
    # benchmark defines its own); these two are beacon's suite-independent
    # claims, one grader across benchmarks.
    exact_verdict = sa.orm.aliased(Verdict)

    stmt = (
        sa.select(
            Run.solution_id,
            Solution.solution_id.label("solution_name"),
            Solution.version.label("solution_version"),
            Run.model_id,
            Run.config_label,
            Run.config_digest,
            engine_expr.label("engine"),
            sa.func.count(sa.func.distinct(Run.id)).label("n_runs"),
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
            # The tolerant reading of the same execution. DISTINCT because the
            # verdict join is otherwise able to multiply outcome counts; the
            # got_facts join is at most one row per result, but the guarantee
            # belongs in the query, not in a comment about today's graders.
            sa.func.count(sa.func.distinct(Result.id))
            .filter(Verdict.bool_value.is_(True))
            .label("n_got_facts"),
            sa.func.count(sa.func.distinct(Result.id))
            .filter(exact_verdict.bool_value.is_(True))
            .label("n_exact"),
            sa.func.percentile_cont(0.5)
            .within_group(Result.tokens_input + Result.tokens_output)
            .label("median_tokens"),
            sa.func.percentile_cont(0.5).within_group(Result.runtime_ms).label("median_runtime"),
        )
        .join(Result, Result.run_id == Run.id)
        .join(Solution, Solution.id == Run.solution_id)
        .join(
            Verdict,
            sa.and_(Verdict.result_id == Result.id, Verdict.metric == "got_facts"),
            isouter=True,
        )
        .join(
            exact_verdict,
            sa.and_(
                exact_verdict.result_id == Result.id,
                exact_verdict.metric == "exact_match",
            ),
            isouter=True,
        )
        .where(*filters)
        .group_by(
            Run.solution_id,
            Solution.solution_id,
            Solution.version,
            Run.model_id,
            Run.config_label,
            Run.config_digest,
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
                exact_rate=(
                    _rate(int(record.n_exact), graded) if int(record.n_exact) else None
                ),
                got_facts_rate=(
                    _rate(int(record.n_got_facts), graded) if int(record.n_got_facts) else None
                ),
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
    level = sa.func.coalesce(EvalItem.item_metadata["difficulty"].astext, "unknown")
    facet_stmt = (
        sa.select(level, sa.func.count())
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
