"""Solution-row upsert + in-process registration for mnemiq SUTs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_storage.repository.project_solutions import ProjectSolutionRepo
from beacon_storage.repository.solutions import SolutionRepo

from beacon_runner.registry import SutRegistry, default_registry

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_storage.models.solutions import Solution
    from sqlalchemy.orm import Session

    from beacon_runner.sut import SolutionUnderTest


def register_mnemiq_solution(
    session: Session,
    *,
    team_id: UUID,
    created_by: UUID,
    sut: SolutionUnderTest,
    project_id: UUID | None = None,
    registry: SutRegistry | None = None,
) -> Solution:
    """Upsert the ``Solution`` row for ``sut`` and register the instance.

    Idempotent: an existing ``(team, solution_id, version)`` row is reused.
    When ``project_id`` is given the solution is linked to the project so runs
    can be created against it. The instance is registered on ``registry``
    (the process-wide default when omitted) so the harness can resolve it.
    """
    identity = sut.identity()
    repo = SolutionRepo(session)
    solution = repo.get_by_team_and_solution(team_id, identity.solution_id, identity.version)
    if solution is None:
        solution = repo.create(
            team_id=team_id,
            solution_id=identity.solution_id,
            version=identity.version,
            owner_team=team_id,
            summary=identity.summary,
            supported_modes=list(identity.supported_modes),
            layers=[layer.model_dump(mode="json") for layer in sut.layers()],
            created_by=created_by,
        )
    if project_id is not None:
        ProjectSolutionRepo(session).link(
            team_id=team_id, project_id=project_id, solution_id=solution.id
        )
    (registry or default_registry()).register(sut)
    return solution
