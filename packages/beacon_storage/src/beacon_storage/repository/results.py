from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from beacon_storage.models.runs import Result, ResultStatus, VerdictOutcome

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class ResultRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        team_id: UUID,
        project_id: UUID,
        run_id: UUID,
        item_id: str,
        attempt_idx: int,
        output: dict[str, object],
        output_kind: str,
        tokens_input: int,
        tokens_output: int,
        runtime_ms: int,
        status: ResultStatus,
        outcome: VerdictOutcome | None,
        error: str | None,
    ) -> Result:
        """Persist a per-item run result and return the saved model."""
        result = Result(
            team_id=team_id,
            project_id=project_id,
            run_id=run_id,
            item_id=item_id,
            attempt_idx=attempt_idx,
            output=output,
            output_kind=output_kind,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            runtime_ms=runtime_ms,
            status=status,
            outcome=outcome,
            error=error,
        )
        self.session.add(result)
        self.session.flush()
        return result

    def get(self, result_id: UUID) -> Result | None:
        """Return the result row with id ``result_id`` or None."""
        return self.session.get(Result, result_id)

    def list_for_run(self, run_id: UUID) -> list[Result]:
        """Return results for ``run_id`` ordered by item then attempt."""
        return list(
            self.session.scalars(
                select(Result)
                .where(Result.run_id == run_id)
                .order_by(Result.item_id, Result.attempt_idx)
            )
        )

    def list_after(self, *, team_id: UUID, after_id: UUID | None, limit: int) -> list[Result]:
        """Return up to ``limit`` results for ``team_id`` with id greater than ``after_id``."""
        stmt = select(Result).where(Result.team_id == team_id).order_by(Result.id).limit(limit)
        if after_id is not None:
            stmt = stmt.where(Result.id > after_id)
        return list(self.session.scalars(stmt))
