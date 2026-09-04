"""Read a run's per-item results: the drill-down behind a score.

Ingestion could write results and nothing could read them back, so a run's
score could be seen but never explained. These are the two reads that make a
number actionable: which questions went which way, and for one question, what
the system answered beside the gold it was graded against.

Nothing here re-executes SQL. The verdict carries what the grader saw at the
time it decided; re-running now would answer from today's database and quietly
describe a different comparison than the one that produced the outcome.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID  # noqa: TC003

import sqlalchemy as sa
from beacon_iam.permissions import Permission
from beacon_storage.models.eval_items import EvalItem
from beacon_storage.models.runs import Result, Verdict
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.runs import RunRepo
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.result import (
    ResultDetailOut,
    ResultListOut,
    ResultRowOut,
    VerdictOut,
)

router = APIRouter(prefix="/v1", tags=["results"])

_SQL_GRADER_KEYS = ("candidate_row_count", "gold_row_count", "mismatch")


def _run_or_404(session: Session, *, run_id: UUID) -> object:
    run = RunRepo(session).get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found")
    return run


def _question(item: EvalItem | None) -> str:
    if item is None:
        return ""
    value = (item.item_input or {}).get("question")
    return str(value) if value is not None else ""


def _metadata(item: EvalItem | None, key: str) -> str | None:
    if item is None:
        return None
    value = (item.item_metadata or {}).get(key)
    return str(value) if value is not None else None


def _execution_evidence(verdicts: list[Verdict]) -> dict[str, Any]:
    """Return the execution grader's raw output, which carries the row counts."""
    for verdict in verdicts:
        raw = verdict.raw_output or {}
        if any(key in raw for key in _SQL_GRADER_KEYS):
            return dict(raw)
    return {}


def _rows_for_run(session: Session, run_id: UUID) -> list[tuple[Result, EvalItem | None]]:
    """Load a run's results with the item each one answers.

    ``Result.item_id`` is a string column, so the join casts rather than
    relying on a foreign key that the schema does not declare. ``eval_items`` is
    bitemporal, so the join also pins the live version -- without that, an item
    with history multiplies every result that references it.
    """
    stmt = (
        sa.select(Result, EvalItem)
        .outerjoin(
            EvalItem,
            sa.and_(
                sa.cast(Result.item_id, sa.Uuid) == EvalItem.item_id,
                EvalItem.valid_to.is_(None),
            ),
        )
        .where(Result.run_id == run_id)
        .order_by(Result.item_id, Result.attempt_idx)
    )
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


def _verdicts_by_result(session: Session, result_ids: list[UUID]) -> dict[UUID, list[Verdict]]:
    if not result_ids:
        return {}
    # Grouped by metric, NEWEST VERSION FIRST WITHIN each metric -- both halves
    # matter and a global `id.desc()` gets it wrong. Verdicts are append-only
    # and versioned, so a result can hold several readings of one metric, and
    # the drill-down picks its got-facts reading with a bare `.find()`:
    # unordered, that is whatever the planner returned, which can be an OLDER
    # version than the matrix shows for the same result, since the matrix
    # defines the current reading as newest-by-id.
    #
    # But ordering by id alone also reorders the METRICS against each other --
    # `exact_match` and `got_facts` from one grading run differ only by
    # insertion id -- which scrambles the presentation for no reason and breaks
    # any caller reading `verdicts[0]`. Sorting by metric first keeps that
    # stable and puts the version ordering where it belongs.
    rows = session.scalars(
        sa.select(Verdict)
        .where(Verdict.result_id.in_(result_ids))
        .order_by(Verdict.result_id, Verdict.metric, Verdict.id.desc())
    ).all()
    grouped: dict[UUID, list[Verdict]] = {}
    for verdict in rows:
        grouped.setdefault(verdict.result_id, []).append(verdict)
    return grouped


@router.get(
    "/runs/{run_id}/results",
    response_model=ResultListOut,
    summary="List a run's per-item results",
)
@requires(Permission.EVAL_VIEW)
def list_results(
    run_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_VIEW, scope_kind="run")),
    ],
    session: Annotated[Session, Depends(get_session)],
    outcome: Annotated[str | None, Query()] = None,
    difficulty: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResultListOut:
    """List a run's results, filtered by outcome and by the item's difficulty."""
    _run_or_404(session, run_id=run_id)
    pairs = _rows_for_run(session, run_id)
    verdicts = _verdicts_by_result(session, [result.id for result, _ in pairs])

    # Facet counts are over the whole run so a filtered view still shows what
    # it is a slice of.
    outcome_counts: dict[str, int] = {}
    difficulty_counts: dict[str, int] = {}
    for result, item in pairs:
        key = str(result.outcome) if result.outcome is not None else "UNGRADED"
        outcome_counts[key] = outcome_counts.get(key, 0) + 1
        level = _metadata(item, "difficulty") or "unknown"
        difficulty_counts[level] = difficulty_counts.get(level, 0) + 1

    selected = [
        (result, item)
        for result, item in pairs
        if (outcome is None or str(result.outcome) == outcome)
        and (difficulty is None or _metadata(item, "difficulty") == difficulty)
    ]

    page = selected[offset : offset + limit]
    rows = []
    for result, item in page:
        evidence = _execution_evidence(verdicts.get(result.id, []))
        mismatch = evidence.get("mismatch")
        facts = next((v for v in verdicts.get(result.id, []) if v.metric == "got_facts"), None)
        rows.append(
            ResultRowOut(
                item_id=UUID(result.item_id),
                question=_question(item),
                difficulty=_metadata(item, "difficulty"),
                outcome=str(result.outcome) if result.outcome is not None else None,
                status=str(result.status),
                got_facts=facts.bool_value if facts is not None else None,
                candidate_row_count=evidence.get("candidate_row_count"),
                gold_row_count=evidence.get("gold_row_count"),
                mismatch_kind=(mismatch or {}).get("kind") if isinstance(mismatch, dict) else None,
                tokens_input=result.tokens_input,
                tokens_output=result.tokens_output,
                runtime_ms=result.runtime_ms,
                created_at=result.created_at,
            )
        )
    return ResultListOut(
        results=rows,
        total=len(selected),
        outcome_counts=outcome_counts,
        difficulty_counts=difficulty_counts,
    )


@router.get(
    "/runs/{run_id}/results/{item_id}",
    response_model=ResultDetailOut,
    summary="One item's answer beside its gold",
)
@requires(Permission.EVAL_VIEW)
def get_result(
    run_id: UUID,
    item_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_VIEW, scope_kind="run")),
    ],
    session: Annotated[Session, Depends(get_session)],
    attempt_idx: Annotated[int | None, Query(ge=0)] = None,
) -> ResultDetailOut:
    """Return one item's output, the gold it was graded against, and every verdict.

    ``attempt_idx`` omitted means "the item's result in this run", latest
    attempt first -- sweep passes store their pass index as the attempt, so a
    hardcoded 0 made every item of every pass>0 run read as missing while the
    run's own listing showed it plainly.
    """
    _run_or_404(session, run_id=run_id)
    stmt = sa.select(Result).where(
        Result.run_id == run_id,
        Result.item_id == str(item_id),
    )
    if attempt_idx is not None:
        stmt = stmt.where(Result.attempt_idx == attempt_idx)
    result = session.scalar(stmt.order_by(Result.attempt_idx.desc()).limit(1))
    if result is None:
        asked = "" if attempt_idx is None else f" attempt {attempt_idx}"
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"no result for item {item_id}{asked} in run {run_id}",
        )
    item = EvalItemRepo(session).get_active(item_id)
    verdicts = _verdicts_by_result(session, [result.id]).get(result.id, [])

    return ResultDetailOut(
        item_id=item_id,
        run_id=run_id,
        question=_question(item),
        evidence=(item.item_input or {}).get("evidence") if item is not None else None,
        difficulty=_metadata(item, "difficulty"),
        database=(item.item_input or {}).get("db_id") if item is not None else None,
        outcome=str(result.outcome) if result.outcome is not None else None,
        status=str(result.status),
        # A deferral is not a failure, and the drill-down has to say which it was.
        deferred=bool((result.output or {}).get("deferred", False))
        or str(result.outcome) == "DEFER",
        error=result.error,
        output=dict(result.output or {}),
        gold=dict(item.gold_answer or {}) if item is not None else {},
        tokens_input=result.tokens_input,
        tokens_output=result.tokens_output,
        runtime_ms=result.runtime_ms,
        verdicts=[
            VerdictOut(
                grader=verdict.grader,
                grader_version=verdict.grader_version,
                metric=verdict.metric,
                criterion=verdict.criterion,
                passed=verdict.bool_value,
                value=verdict.value,
                justification=verdict.justification,
                evidence=dict(verdict.raw_output) if verdict.raw_output else None,
            )
            for verdict in verdicts
        ],
    )
