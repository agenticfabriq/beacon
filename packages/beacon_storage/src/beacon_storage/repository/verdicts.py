from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from beacon_storage.models.runs import Result, Run, Trace, Verdict

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class VerdictForEvalItem:
    solution_id: UUID
    answer_hash: str
    canonical_answer: dict[str, object]
    confidence: float


class VerdictRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        team_id: UUID,
        result_id: UUID,
        grader: str,
        grader_version: str,
        criterion: str,
        metric: str | None = None,
        bool_value: bool | None,
        value: float | None,
        justification: str | None,
        raw_output: dict[str, object] | None,
        canonical_answer: dict[str, object] | None = None,
        answer_hash: str | None = None,
        confidence: float | None = None,
    ) -> Verdict:
        """Persist a verdict on ``result_id`` and return the saved model."""
        verdict = Verdict(
            team_id=team_id,
            result_id=result_id,
            grader=grader,
            grader_version=grader_version,
            criterion=criterion,
            metric=metric,
            bool_value=bool_value,
            value=value,
            justification=justification,
            raw_output=raw_output,
            canonical_answer=canonical_answer,
            answer_hash=answer_hash,
            confidence=confidence,
        )
        self.session.add(verdict)
        self.session.flush()
        return verdict

    def list_for_result(self, result_id: UUID) -> list[Verdict]:
        """Return verdicts for ``result_id`` ordered by grader then criterion."""
        return list(
            self.session.scalars(
                select(Verdict)
                .where(Verdict.result_id == result_id)
                .order_by(Verdict.grader, Verdict.criterion)
            )
        )

    def list_for_trace(self, trace_id: UUID) -> list[Verdict]:
        """Return verdicts joined through the trace identified by ``trace_id``."""
        return list(
            self.session.scalars(
                select(Verdict)
                .join(Trace, Trace.result_id == Verdict.result_id)
                .where(Trace.id == trace_id)
                .order_by(Verdict.grader, Verdict.criterion)
            )
        )

    def list_for_eval_item(self, *, team_id: UUID, eval_item_id: UUID) -> list[VerdictForEvalItem]:
        """Return per-solution canonical answers and confidences for ``eval_item_id``."""
        rows = self.session.execute(
            select(
                Run.solution_id,
                Verdict.answer_hash,
                Verdict.canonical_answer,
                Verdict.confidence,
            )
            .join(Result, Result.id == Verdict.result_id)
            .join(Run, Run.id == Result.run_id)
            .where(
                Verdict.team_id == team_id,
                Result.item_id == str(eval_item_id),
                Verdict.answer_hash.is_not(None),
            )
            .order_by(Result.id, Verdict.grader, Verdict.criterion)
        )
        return [
            VerdictForEvalItem(
                solution_id=solution_id,
                answer_hash=answer_hash,
                canonical_answer=canonical_answer or {},
                confidence=float(confidence or 0.0),
            )
            for solution_id, answer_hash, canonical_answer, confidence in rows
            if answer_hash is not None
        ]
