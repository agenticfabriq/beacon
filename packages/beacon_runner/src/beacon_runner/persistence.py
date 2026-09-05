"""Translate runner outputs into Postgres rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_graders.types import Verdict as GraderVerdict
from beacon_graders.types import VerdictOutcome as GraderOutcome
from beacon_storage.models.runs import ResultStatus
from beacon_storage.models.runs import VerdictOutcome as StorageOutcome
from beacon_storage.repository.outcome_history import OutcomeHistoryRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.traces import TraceRepo
from beacon_storage.repository.verdicts import VerdictRepo

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

    from beacon_runner.types import EvalItem, ExecutionResult

_OUTCOME_TO_STATUS: dict[GraderOutcome, ResultStatus] = {
    GraderOutcome.PASS: ResultStatus.COMPLETED,
    GraderOutcome.FAIL: ResultStatus.COMPLETED,
    # The attempt completed; the solution declined. Not an error status.
    GraderOutcome.DEFER: ResultStatus.COMPLETED,
    GraderOutcome.ERROR: ResultStatus.ERROR,
    GraderOutcome.TIMEOUT: ResultStatus.TIMEOUT,
}


def persist_result(
    session: Session,
    *,
    team_id: UUID,
    run_id: UUID,
    item: EvalItem,
    attempt_idx: int,
    exec_result: ExecutionResult,
    verdicts: list[GraderVerdict],
    outcome: GraderOutcome,
    deciding: GraderVerdict | None,
    source: str,
) -> None:
    """Persist one runner result, its grader verdicts, and trace tree.

    ``deciding`` is the verdict the outcome followed, as reported by
    ``VerdictComposer.compose_with_deciding`` -- the only thing that knows,
    because only it knows which BRANCH decided. Asking the verdict list
    instead names a grader for a deferred or errored outcome it had no say in;
    the composer's ``_deciding_verdict`` is private for exactly that reason.
    Passed in rather than re-derived here: "the execution verdict carrying the
    primary metric with a usable bool" is the composer's rule, and every place
    that has re-implemented one of the graders' own questions has ended up
    asking a wider one (B77). ``None`` means no grader decided, which is a real
    and common case -- an error, a timeout, a refusal contract, a judge-only
    rubric -- and is recorded as a derivation naming no grader rather than
    guessing one.

    Both ``deciding`` and ``source`` are REQUIRED, with no defaults, and that
    is the point. A default ``deciding=None`` records "no grader decided"
    about an outcome a grader decided: a valid row that lies, which is how the
    harness path shipped wrong past a green suite. A default ``source``
    mislabels wherever the next caller comes from. Neither can now be omitted
    by accident.
    """
    result_row = ResultRepo(session).create(
        team_id=team_id,
        run_id=run_id,
        item_id=item.item_id,
        attempt_idx=attempt_idx,
        output=exec_result.output,
        output_kind=exec_result.output_kind,
        tokens_input=exec_result.tokens_input,
        tokens_output=exec_result.tokens_output,
        runtime_ms=exec_result.runtime_ms,
        status=_OUTCOME_TO_STATUS[outcome],
        outcome=StorageOutcome(outcome.value),
        error=exec_result.error,
    )

    # The FIRST outcome, recorded as history so that a later regrade cannot
    # move a published number without the previous value surviving. Written
    # here rather than in either caller because this is the single path both
    # ingest and the harness take to create a result.
    OutcomeHistoryRepo(session).record(
        team_id=team_id,
        result_id=result_row.id,
        outcome=outcome.value,
        source=source,
        grader=deciding.grader if deciding is not None else None,
        grader_version=deciding.grader_version if deciding is not None else None,
        metric=deciding.metric if deciding is not None else None,
    )

    verdict_repo = VerdictRepo(session)
    for verdict in verdicts:
        verdict_repo.create(
            team_id=team_id,
            result_id=result_row.id,
            grader=verdict.grader,
            grader_version=verdict.grader_version,
            criterion=verdict.criterion,
            metric=verdict.metric,
            bool_value=verdict.bool_value,
            value=verdict.value,
            justification=verdict.justification,
            raw_output=verdict.raw_output,
        )

    TraceRepo(session).create(
        team_id=team_id,
        result_id=result_row.id,
        step_tree=exec_result.trace.to_dict(),
        object_storage_uri=None,
    )
