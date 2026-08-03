from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from beacon_storage.errors import ConflictingSolutionDeclarationError
from beacon_storage.models.solutions import Solution

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


def _layer_names(layers: list[dict[str, object]] | None) -> set[str]:
    """The layer names a declaration carries.

    Only the names are compared. Descriptions are prose, but the name is the
    key the attribution engine ablates by, so a renamed layer is a different
    experiment even when the code behind it did not change.
    """
    return {str(layer.get("name", "")) for layer in (layers or []) if layer.get("name")}


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

    def ensure_declared(
        self,
        *,
        team_id: UUID,
        solution_id: str,
        version: str,
        summary: str,
        supported_modes: list[str],
        layers: list[dict[str, object]],
        created_by: UUID,
    ) -> tuple[Solution, bool]:
        """Register what a runner declares about itself, or reuse the match.

        Beacon does not execute, so the runner is the only thing that knows its
        own identity, version and layers; making a human retype them in a form
        guarantees drift. The contract is the one result ingestion already uses:
        first declaration creates, an identical one is a no-op, and a divergent
        one is refused.

        Returns the row and whether it was created.
        """
        existing = self.get_by_team_and_solution(team_id, solution_id, version)
        if existing is None:
            created = self.create(
                team_id=team_id,
                solution_id=solution_id,
                version=version,
                owner_team=team_id,
                summary=summary,
                supported_modes=supported_modes,
                layers=layers,
                created_by=created_by,
            )
            return created, True

        registered_names = _layer_names(existing.layers)
        declared_names = _layer_names(layers)
        if registered_names != declared_names:
            raise ConflictingSolutionDeclarationError(
                f"{solution_id}@{version} is already registered with layers "
                f"{sorted(registered_names)}; this push declares "
                f"{sorted(declared_names)}. A version's layers are part of what "
                f"its scores mean, so they are not rewritten after the fact -- "
                f"publish a new version instead.",
                registered=sorted(registered_names),
                declared=sorted(declared_names),
            )
        return existing, False

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
