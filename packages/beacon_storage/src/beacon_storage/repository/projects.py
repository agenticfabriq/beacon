from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from beacon_storage.models.tenancy import Project

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class ProjectRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        team_id: UUID,
        name: str,
        created_by: UUID,
        description: str | None = None,
    ) -> Project:
        """Persist a new project owned by ``team_id`` and return it."""
        p = Project(team_id=team_id, name=name, description=description, created_by=created_by)
        self.session.add(p)
        self.session.flush()
        return p

    def get(self, project_id: UUID) -> Project | None:
        """Return the project with id ``project_id`` or None."""
        return self.session.get(Project, project_id)

    def list_for_team(self, team_id: UUID, include_archived: bool = False) -> list[Project]:
        """Return projects under ``team_id``, optionally including archived ones."""
        stmt = select(Project).where(Project.team_id == team_id)
        if not include_archived:
            stmt = stmt.where(Project.archived_at.is_(None))
        return list(self.session.scalars(stmt.order_by(Project.name)))
