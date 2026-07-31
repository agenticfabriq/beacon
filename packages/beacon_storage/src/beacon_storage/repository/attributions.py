"""Attribution repository."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from beacon_storage.models.attribution import Attribution

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class AttributionRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def latest_for(
        self,
        *,
        project_id: UUID,
        solution_id: UUID,
        suite: str,
    ) -> list[Attribution]:
        """Return attributions from the most recent sweep for the given scope."""
        latest_sweep_id = self.session.scalar(
            select(Attribution.sweep_id)
            .where(
                Attribution.project_id == project_id,
                Attribution.solution_id == solution_id,
                Attribution.suite == suite,
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
                    Attribution.project_id == project_id,
                    Attribution.solution_id == solution_id,
                    Attribution.suite == suite,
                    Attribution.sweep_id == latest_sweep_id,
                )
                .order_by(Attribution.layer_name)
            )
        )
