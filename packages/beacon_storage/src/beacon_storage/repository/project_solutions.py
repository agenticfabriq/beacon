from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from beacon_storage.models.project_solutions import ProjectSolution
from beacon_storage.models.solutions import Solution

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.orm import Session


class ProjectSolutionRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def link(self, *, team_id: UUID, project_id: UUID, solution_id: UUID) -> ProjectSolution:
        """Attach ``solution_id`` to ``project_id`` or return the existing link."""
        existing = self.session.get(ProjectSolution, (project_id, solution_id))
        if existing is not None:
            return existing

        link = ProjectSolution(
            team_id=team_id,
            project_id=project_id,
            solution_id=solution_id,
        )
        self.session.add(link)
        self.session.flush()
        return link

    def unlink(self, *, project_id: UUID, solution_id: UUID) -> None:
        """Remove the project-solution association if present."""
        self.session.execute(
            delete(ProjectSolution).where(
                ProjectSolution.project_id == project_id,
                ProjectSolution.solution_id == solution_id,
            )
        )

    def list_with_solution(self, project_id: UUID) -> list[tuple[Solution, datetime]]:
        """Return solutions linked to ``project_id`` paired with their ``added_at`` timestamp."""
        rows = self.session.execute(
            select(Solution, ProjectSolution.added_at)
            .join(ProjectSolution, ProjectSolution.solution_id == Solution.id)
            .where(ProjectSolution.project_id == project_id)
            .order_by(Solution.solution_id, Solution.version)
        )
        return [(solution, added_at) for solution, added_at in rows]
