"""Translate runner outputs into Postgres rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_graders.types import Verdict as GraderVerdict
from beacon_graders.types import VerdictOutcome as GraderOutcome
from beacon_storage.models.runs import ResultStatus
from beacon_storage.models.runs import VerdictOutcome as StorageOutcome
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
    project_id: UUID,
    run_id: UUID,
    item: EvalItem,
    attempt_idx: int,
    exec_result: ExecutionResult,
    verdicts: list[GraderVerdict],
    outcome: GraderOutcome,
) -> None:
    """Persist one runner result, its grader verdicts, and trace tree."""
    result_row = ResultRepo(session).create(
        team_id=team_id,
        project_id=project_id,
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

    verdict_repo = VerdictRepo(session)
    for verdict in verdicts:
        verdict_repo.create(
            team_id=team_id,
            project_id=project_id,
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
        project_id=project_id,
        result_id=result_row.id,
        step_tree=exec_result.trace.to_dict(),
        object_storage_uri=None,
    )
