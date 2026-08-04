"""Push execution outputs into a registered run, and close it.

Beacon registers a run, someone else executes it on their own hardware, and the
outputs come back here to be graded. The system under test never grades itself:
what arrives is `output`, and the verdicts are computed on this side.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import sqlalchemy as sa
from beacon_graders.composer import VerdictComposer
from beacon_graders.graders import DabstepAnswerMatcher, ResultSetMatchGrader
from beacon_graders.types import VerdictOutcome
from beacon_iam.permissions import Permission
from beacon_runner.composer_factory import composer_for_suite, graders_for_suite
from beacon_runner.persistence import persist_result
from beacon_runner.trace_conformance import layer_contradictions
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep
from beacon_storage.models.runs import Result, RunStatus
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.config import ApiConfig
from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.ingest import ResultIngestIn, ResultIngestOut, RunCompleteOut

router = APIRouter(prefix="/v1", tags=["ingest"])

_INGESTABLE_STATUSES = frozenset({RunStatus.PENDING, RunStatus.RUNNING})


def _status_value(status_like: object) -> str:
    """Read a status as a string.

    These columns are String, so SQLAlchemy hands back a plain ``str`` rather
    than the StrEnum member the model annotates.
    """
    return str(getattr(status_like, "value", status_like))


def _engine_for_suite(suite: str) -> sa.Engine | None:
    """Return the benchmark database this suite's graders execute against."""
    url = ApiConfig().benchmark_db_urls.get(suite)
    return sa.create_engine(url, pool_pre_ping=True) if url else None


def _answer_authority_applies(item: EvalItem, body: ResultIngestIn) -> bool:
    """Whether this push grades as rows-against-materialized-gold.

    The answer-authority path needs both halves: the push carries the rows its
    engine returned, and the item's gold was materialized at import. Either
    half missing falls back to the execution path (until it is retired).
    """
    grader = ResultSetMatchGrader()
    exec_result = ExecutionResult(
        output=body.output,
        output_kind=body.output_kind,
        trace=_trace_step(body),
        tokens_input=body.tokens_input,
        tokens_output=body.tokens_output,
        runtime_ms=body.runtime_ms,
        error=body.error,
        deferred=body.deferred,
    )
    return grader.applicable(item, exec_result)


def _composer_for(suite: str) -> VerdictComposer:
    """Build the suite's composer, refusing to grade blind.

    A suite whose graders execute SQL cannot be graded without the database to
    execute against. Composing anyway would mark every item ERROR and store it
    as though the solution had failed, so this fails loudly instead.
    """
    if _engine_for_suite(suite) is None and graders_for_suite(suite, engine=None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"suite {suite!r} is graded by execution graders but no benchmark database is "
            "configured for it; set BEACON_BENCHMARK_DB_URLS",
        )
    return composer_for_suite(
        suite,
        engine=_engine_for_suite(suite),
        # A suite no benchmark adapter claims still needs something to grade
        # with, or every item composes ERROR and reads as a failed solution.
        fallback=[DabstepAnswerMatcher()],
    )


def _eval_item(session: Session, *, item_id: str, suite: str) -> EvalItem:
    """Load the gold for an item, which beacon owns and the pusher does not."""
    try:
        parsed = UUID(item_id)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"item_id {item_id!r} is not a UUID"
        ) from exc
    row = EvalItemRepo(session).get_active(parsed)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"eval item {item_id} not found")
    if row.suite != suite:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"eval item {item_id} belongs to suite {row.suite!r}, not {suite!r}",
        )
    return EvalItem(
        item_id=str(row.item_id),
        suite=row.suite,
        query=row.item_input,
        ground_truth=row.gold_answer or {},
        metadata=row.item_metadata,
    )


def _existing_result(session: Session, *, run_id: UUID, body: ResultIngestIn) -> Result | None:
    return session.scalar(
        sa.select(Result).where(
            Result.run_id == run_id,
            Result.item_id == body.item_id,
            Result.attempt_idx == body.attempt_idx,
        )
    )


def _assert_trace_matches_declared_config(
    body: ResultIngestIn, *, run_config: dict[str, object]
) -> None:
    """Refuse a result whose trace contradicts the arm the run declared.

    A run that says self_consistency was off, pushing a trace in which it ran,
    is not the experiment it claims to be -- and the attribution engine would
    compare it against a baseline as though it were.
    """
    if body.trace is None:
        return
    declared = run_config.get("layers_enabled")
    if not isinstance(declared, dict) or not declared:
        return
    contradictions = layer_contradictions(
        ExecutionStep.model_validate(body.trace),
        {str(k): bool(v) for k, v in declared.items()},
    )
    if contradictions:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "the pushed trace contradicts this run's declared configuration -- "
            + "; ".join(contradictions),
        )


def _trace_step(body: ResultIngestIn) -> ExecutionStep:
    if body.trace is not None:
        return ExecutionStep.model_validate(body.trace)
    return ExecutionStep(
        uuid=f"ingested-{body.item_id}",
        name="ingested",
        level="workflow",
        status="FAILED" if body.error else "COMPLETED",
    )


@router.post(
    "/runs/{run_id}/results",
    response_model=ResultIngestOut,
    summary="Push one item's execution output into a run",
)
@requires(Permission.EVAL_RUN)
def ingest_result(
    run_id: UUID,
    body: ResultIngestIn,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_RUN, scope_kind="run")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> ResultIngestOut:
    """Grade one pushed output and persist it against the run."""
    run = RunRepo(session).get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found")
    if _status_value(run.status) not in {_status_value(s) for s in _INGESTABLE_STATUSES}:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"run {run_id} is {_status_value(run.status)}; results can only be pushed to "
            f"a run awaiting them "
            f"({', '.join(sorted(_status_value(s) for s in _INGESTABLE_STATUSES))})",
        )

    if (existing := _existing_result(session, run_id=run_id, body=body)) is not None:
        # A retrying client that lost our response may push the same payload
        # again; that must not fail. A *different* payload for the same attempt
        # is a real conflict, and silently overwriting it would destroy the
        # evidence a verdict was computed from.
        if _payload_matches(existing, body):
            return ResultIngestOut(
                item_id=body.item_id,
                attempt_idx=body.attempt_idx,
                outcome=_status_value(existing.outcome) if existing.outcome else "unknown",
                created=False,
            )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"item {body.item_id} attempt {body.attempt_idx} already has a different result "
            f"in run {run_id}; push a new attempt_idx rather than replacing it",
        )

    item = _eval_item(session, item_id=body.item_id, suite=run.suite)
    _assert_trace_matches_declared_config(body, run_config=run.config)
    exec_result = ExecutionResult(
        output=body.output,
        output_kind=body.output_kind,
        trace=_trace_step(body),
        tokens_input=body.tokens_input,
        tokens_output=body.tokens_output,
        runtime_ms=body.runtime_ms,
        error=body.error,
        deferred=body.deferred,
    )

    if _answer_authority_applies(item, body):
        # The push carries its engine's rows and the gold is materialized:
        # grade by comparison, no benchmark database involved.
        composer = VerdictComposer(graders=[ResultSetMatchGrader()])
    else:
        composer = _composer_for(run.suite)
    try:
        verdicts, outcome = composer.compose(item, exec_result)
    except Exception as exc:  # noqa: BLE001 - a grader fault is not the pusher's fault
        verdicts, outcome = [], VerdictOutcome.ERROR
        exec_result = exec_result.model_copy(
            update={"error": f"grading_raised: {type(exc).__name__}: {exc!s}"[:1000]}
        )

    if _status_value(run.status) == RunStatus.PENDING.value:
        RunRepo(session).mark_running(run_id)
    persist_result(
        session,
        team_id=run.team_id,
        run_id=run_id,
        item=item,
        attempt_idx=body.attempt_idx,
        exec_result=exec_result,
        verdicts=verdicts,
        outcome=outcome,
    )
    session.commit()
    return ResultIngestOut(
        item_id=body.item_id,
        attempt_idx=body.attempt_idx,
        outcome=outcome.value,
        created=True,
    )


def _payload_matches(existing: Result, body: ResultIngestIn) -> bool:
    """Return whether a re-push is byte-identical to what is already stored."""
    stored: dict[str, Any] = dict(existing.output)
    return (
        stored == body.output
        and existing.output_kind == body.output_kind
        and existing.tokens_input == body.tokens_input
        and existing.tokens_output == body.tokens_output
        and existing.runtime_ms == body.runtime_ms
        and (existing.error or None) == (body.error or None)
    )


@router.post(
    "/runs/{run_id}/complete",
    response_model=RunCompleteOut,
    summary="Declare a run finished",
)
@requires(Permission.EVAL_RUN)
def complete_run(
    run_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_RUN, scope_kind="run")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> RunCompleteOut:
    """Close a run once its pusher has no more results to send."""
    run = RunRepo(session).get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found")

    n_results = len(ResultRepo(session).list_for_run(run_id))
    if _status_value(run.status) in {_status_value(s) for s in _INGESTABLE_STATUSES}:
        RunRepo(session).mark_completed(run_id)
        session.commit()
    refreshed = RunRepo(session).get(run_id)
    assert refreshed is not None
    return RunCompleteOut(
        run_id=str(run_id),
        status=_status_value(refreshed.status),
        n_results=n_results,
    )
