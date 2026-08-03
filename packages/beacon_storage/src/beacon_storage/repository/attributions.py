"""Attribution repository."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from beacon_storage.models.attribution import Attribution
from beacon_storage.models.runs import Run

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class AttributionRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def latest_for(
        self,
        *,
        team_id: UUID,
        solution_id: UUID,
        suite: str,
    ) -> list[Attribution]:
        """Return attributions from the most recent sweep for the given scope.

        A snapshot whose baseline or ablated run has been invalidated is skipped
        with the sweep it belongs to: a layer effect is a claim about two runs,
        so retiring either retires the claim. Without this, invalidating a bad
        run leaves its attribution standing as the latest word.
        """
        invalidated = select(Run.id).where(Run.invalidated_at.is_not(None)).scalar_subquery()
        latest_sweep_id = self.session.scalar(
            select(Attribution.sweep_id)
            .where(
                Attribution.team_id == team_id,
                Attribution.solution_id == solution_id,
                Attribution.suite == suite,
                Attribution.baseline_run_id.not_in(invalidated),
                Attribution.ablated_run_id.not_in(invalidated),
            )
            .order_by(Attribution.created_at.desc(), Attribution.attribution_id.desc())
            .limit(1)
        )
        if latest_sweep_id is None:
            return []

        return list(
            self.session.scalars(
                select(Attribution)
                .where(
                    Attribution.team_id == team_id,
                    Attribution.solution_id == solution_id,
                    Attribution.suite == suite,
                    Attribution.sweep_id == latest_sweep_id,
                    Attribution.baseline_run_id.not_in(invalidated),
                    Attribution.ablated_run_id.not_in(invalidated),
                )
                .order_by(Attribution.layer_name)
            )
        )
