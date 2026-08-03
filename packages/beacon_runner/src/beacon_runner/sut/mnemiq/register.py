"""Solution-row upsert + in-process registration for mnemiq SUTs."""

from __future__ import annotations

from typing import TYPE_CHECKING

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
    registry: SutRegistry | None = None,
) -> Solution:
    """Upsert the ``Solution`` row for ``sut`` and register the instance.

    Idempotent: an existing ``(team, solution_id, version)`` row is reused.
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
    (registry or default_registry()).register(sut)
    return solution
