from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from beacon_storage.models.solutions import Solution

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class SolutionRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        team_id: UUID,
        solution_id: str,
        version: str,
        owner_team: UUID,
        summary: str,
        supported_modes: list[str],
        layers: list[dict[str, object]],
        created_by: UUID,
    ) -> Solution:
        """Register a new solution version for ``team_id`` and return it."""
        solution = Solution(
            team_id=team_id,
            solution_id=solution_id,
            version=version,
            owner_team=owner_team,
            summary=summary,
            supported_modes=supported_modes,
            layers=layers,
            created_by=created_by,
        )
        self.session.add(solution)
        self.session.flush()
        return solution

    def get(self, pk: UUID) -> Solution | None:
        """Return the solution row with primary key ``pk`` or None."""
        return self.session.get(Solution, pk)

    def get_by_team_and_solution(
        self,
        team_id: UUID,
        solution_id: str,
        version: str,
    ) -> Solution | None:
        """Return the solution row matching the ``(team_id, solution_id, version)`` triple."""
        return self.session.scalar(
            select(Solution).where(
                Solution.team_id == team_id,
                Solution.solution_id == solution_id,
                Solution.version == version,
            )
        )

    def list_for_team(self, team_id: UUID) -> list[Solution]:
        """Return solutions owned by ``team_id`` ordered by solution id and version."""
        return list(
            self.session.scalars(
                select(Solution)
                .where(Solution.team_id == team_id)
                .order_by(Solution.solution_id, Solution.version)
            )
        )
