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
from beacon_graders.graders.result_set_match import explicit_columns, gold_variants, rows_from
from beacon_graders.types import VerdictOutcome
from beacon_iam.permissions import Permission
from beacon_runner.persistence import persist_result
from beacon_runner.trace_conformance import layer_contradictions
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep
from beacon_storage.models.runs import Result, RunStatus
from beacon_storage.models.suites import Suite
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.ingest import (
    ResultIngestIn,
    ResultIngestOut,
    RunCompleteIn,
    RunCompleteOut,
)

router = APIRouter(prefix="/v1", tags=["ingest"])

_INGESTABLE_STATUSES = frozenset({RunStatus.PENDING, RunStatus.RUNNING})


def _status_value(status_like: object) -> str:
    """Read a status as a string.

    These columns are String, so SQLAlchemy hands back a plain ``str`` rather
    than the StrEnum member the model annotates.
    """
    return str(getattr(status_like, "value", status_like))


def _headline_metric(session: Session, suite_id: UUID) -> str | None:
    """The metric this suite declares as its headline, or None for the default.

    Read at ingest so an outcome is composed under the same reading every
    aggregate and every regrade uses. Without it the outcome followed the
    grader's default while `scripts/regrade_suite.py` followed the
    declaration, so the same result got different outcomes depending on how it
    was graded (B74).

    None on a missing suite or a missing key rather than raising: a suite that
    declares no headline wants the grader's default, and a push must not fail
    because metadata is silent.
    """
    suite = session.get(Suite, suite_id)
    if suite is None:
        return None
    declared = (suite.suite_metadata or {}).get("headline_metric")
    return str(declared) if declared else None


def _composer_for(
    item: EvalItem, body: ResultIngestIn, *, headline: str | None = None
) -> VerdictComposer:
    """Build the composer for one push, refusing to grade blind.

    SQL grades by result-set comparison: the push carries the rows its engine
    returned and the item's gold carries rows materialized at import. Beacon
    executes nothing to grade. A SQL push missing either half is refused
    loudly -- composing anyway would mark the item ERROR and store it as
    though the solution had failed.
    """
    if body.output_kind != "sql" or body.deferred or body.error:
        # Answer-and-friends grade by the matcher; deferrals and errored
        # attempts never need rows -- the composer scores them DEFER/ERROR
        # before any grader runs.
        # One reading, so nothing to choose between: the answer matcher emits
        # a single metric and the headline cannot select a different one.
        return VerdictComposer(graders=[DabstepAnswerMatcher()])

    # An item DECLARING itself unanswerable is judged on its REFUSAL, not on a
    # result set, so neither gate below applies to it -- and this is not a
    # loophole, it is the one case where absent gold and absent rows are both
    # legitimate. The composer short-circuits such items ahead of pass/fail:
    # deferring is PASS, answering anyway is FAIL.
    #
    # Both gates have to be skipped, not just the gold one. `ingest_payload`
    # writes `output["rows"]` only when the harness captured engine rows, while
    # `output_kind` is unconditionally "sql" -- so a SUT that over-answers an
    # unanswerable item WITHOUT captured rows carries neither half, and gating
    # on either one refuses the push. `load_eval_reports.py` calls
    # `raise_for_status`, so one such item aborts the entire load, and the band
    # whose stated purpose is detecting over-answering is the band that cannot
    # record one.
    #
    # Graders still run as evidence where gold happens to exist; the composer
    # reaches its answerable branch before consulting their verdicts.
    #
    # Missing gold WITHOUT that declaration stays an ingest defect and is still
    # refused -- two different absences, and only the declared one is benign.
    if (item.query or {}).get("answerable") is False:
        return VerdictComposer(graders=[ResultSetMatchGrader()], primary_metric=headline or None)

    # Both predicates below are the grader's own, not lookalikes. This gate
    # selects ResultSetMatchGrader, whose `applicable` is
    # `rows_from(...) is not None and bool(gold_variants(...))` --
    # and `gold_variants` reads `accepted_results` first, treating BIRD's single
    # `rows`/`columns` as the one-element case. Checking `gold["rows"]` here
    # asked a different question than the grader it guards: it refused every
    # SQL push for `spider2_lite_local_v1` (135 items) and `fs_payments_v1`
    # (24 gradeable), both of which store gold as `accepted_results` and both
    # of which this grader was built to read. Only BIRD passed, because BIRD is
    # the shape the check was written against.
    #
    # Widening cannot regress BIRD: it reaches `gold_variants` either way.
    if not gold_variants(item.ground_truth or {}):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"item {item.item_id} carries no gold result set: neither "
            "`accepted_results` nor `rows`, and it does not declare itself "
            "unanswerable. This is a curation gap in the item. Materializing "
            "is only meaningful where the suite treats gold SQL as "
            "authoritative -- it is not for spider2_lite, whose `sql` is "
            "documentation only -- and scripts/materialize_gold.py sweeps a "
            "whole SUITE, rewriting every item it can execute, so do not reach "
            "for it to repair one item.",
        )
    # The grader's own predicate again, on the output side. `isinstance(...,
    # list)` is WIDER than this: `[1, 2, 3]` is a list and clears it, then
    # ResultSetMatchGrader.applicable rejects the same payload, nothing emits a
    # verdict, and the composer falls through to ERROR -- which reads as a
    # crashed harness rather than a malformed push.
    if rows_from(body.output.get("rows")) is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "a SQL push must carry the rows its engine returned in output.rows "
            "(with output.row_count when they are a bounded preview); beacon "
            "grades by comparison and does not execute SQL. Each row must be a "
            "list of values or an object keyed by column name -- a flat list of "
            "scalars is not a row set.",
        )
    # The SUITE says which reading decides its outcomes. Hardcoding the
    # grader's default meant a suite declaring the tolerant reading as its
    # headline still had its outcomes composed under the strict one, so the
    # outcome disagreed with every aggregate and with any regrade -- which is
    # what a regrade of spider2_lite_local_v1 surfaced as 3 FAIL -> PASS
    # flips, every case where the two readings differ (B74). The declaration
    # is deliberate: "strict for BIRD, tolerant for Spider -- each benchmark
    # defines its own".
    return VerdictComposer(graders=[ResultSetMatchGrader()], primary_metric=headline or None)


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
        output=_stamped_output(body.output, deferred=body.deferred),
        output_kind=body.output_kind,
        trace=_trace_step(body),
        tokens_input=body.tokens_input,
        tokens_output=body.tokens_output,
        runtime_ms=body.runtime_ms,
        error=body.error,
        deferred=body.deferred,
    )

    composer = _composer_for(item, body, headline=_headline_metric(session, run.suite_id))
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


def _stamped_output(output: dict[str, Any], *, deferred: bool = False) -> dict[str, Any]:
    """The push's output with the facts grading needs made durable.

    Dict-shaped rows carry their SELECT order only while in flight: JSONB
    canonicalizes object keys at rest, and column order is part of exact
    match. Stamp an ordered ``columns`` array now, while the order is still
    the wire's, so the stored evidence can be regraded faithfully later.

    A declined answer is stamped for the same reason. ``deferred`` arrives as
    a first-class field of the push, but only ``output`` is persisted, and a
    regrade rebuilds its ExecutionResult from ``output`` alone. Left unstamped,
    a refusal graded PASS at ingest would come back FAIL the next time the
    suite was regraded -- and on an unanswerable item that inversion is the
    whole measurement. Every SUT here happens to mirror the flag itself, which
    is precisely why the gap was invisible.
    """
    stamped = dict(output)
    rows = output.get("rows")
    # Usable to the GRADER, not merely present: a truthy `columns` the grader
    # cannot read (`[0, 1]`) would suppress the stamp and store evidence that
    # looks declared and regrades on insertion order. Stamping over it loses
    # nothing -- the grader ignores it either way -- and recovers the wire
    # order while it still exists.
    if (
        isinstance(rows, list)
        and rows
        and isinstance(rows[0], dict)
        and explicit_columns(output.get("columns")) is None
    ):
        stamped["columns"] = list(rows[0].keys())
    if deferred:
        stamped["deferred"] = True
    return stamped


def _payload_matches(existing: Result, body: ResultIngestIn) -> bool:
    """Return whether a re-push is byte-identical to what is already stored."""
    stored: dict[str, Any] = dict(existing.output)
    return (
        stored == _stamped_output(body.output, deferred=body.deferred)
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
    body: RunCompleteIn | None = None,
) -> RunCompleteOut:
    """Close a run, as complete or -- with a reason -- as FAILED.

    The failure path exists because a pusher had no way to end a run badly.
    `invalidate` records a reason and stops a run mid-flight, but it requires
    EVAL_MANAGE and a TEAM_MEMBER pusher holds only EVAL_RUN, so a load that
    hit an unrecoverable refusal could either claim success or leave the run
    RUNNING for ever. It left it RUNNING (B71).

    Closing YOUR OWN run as failed is the same authority as closing it as
    complete, which is why it lives here under EVAL_RUN rather than becoming a
    second manage-level endpoint. Retiring somebody ELSE'S run is what
    `invalidate` is for, and that stays EVAL_MANAGE.
    """
    run = RunRepo(session).get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found")

    n_results = len(ResultRepo(session).list_for_run(run_id))
    # Only from a status that was still accepting results: a run someone has
    # already closed must not be reopened and re-closed under a new verdict.
    if _status_value(run.status) in {_status_value(s) for s in _INGESTABLE_STATUSES}:
        failure = body.error if body is not None else None
        if failure is not None:
            RunRepo(session).mark_failed(run_id, failure)
        else:
            RunRepo(session).mark_completed(run_id)
        session.commit()
    refreshed = RunRepo(session).get(run_id)
    assert refreshed is not None
    return RunCompleteOut(
        run_id=str(run_id),
        status=_status_value(refreshed.status),
        n_results=n_results,
    )
