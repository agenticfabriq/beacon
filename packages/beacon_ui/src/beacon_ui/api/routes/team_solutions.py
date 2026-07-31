"""Team solution catalog API routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003

from beacon_iam.permissions import Permission
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.solutions import SolutionRepo
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.team_solution import TeamSolutionIn, TeamSolutionOut

router = APIRouter(prefix="/v1/teams", tags=["team-solutions"])

if TYPE_CHECKING:
    from beacon_storage.models.solutions import Solution
    from beacon_storage.models.tenancy import User


def _solution_out(solution: Solution) -> TeamSolutionOut:
    return TeamSolutionOut(
        id=solution.id,
        solution_id=solution.solution_id,
        name=solution.solution_id,
        version=solution.version,
        owner_team=solution.owner_team,
        summary=solution.summary,
        supported_modes=solution.supported_modes,
        layers=solution.layers,
        created_by=solution.created_by,
        created_at=solution.created_at,
    )


@router.get(
    "/{team_id}/solutions",
    response_model=list[TeamSolutionOut],
    summary="List team solution catalog entries",
)
@requires(Permission.TEAM_VIEW)
def list_team_solutions(
    team_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.TEAM_VIEW, scope_kind="team")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> list[TeamSolutionOut]:
    """List solution catalog entries owned by the team."""
    return [_solution_out(solution) for solution in SolutionRepo(session).list_for_team(team_id)]


@router.post(
    "/{team_id}/solutions",
    response_model=TeamSolutionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a team solution catalog entry",
)
@requires(Permission.SOLUTION_REGISTER)
def register_team_solution(
    team_id: UUID,
    body: TeamSolutionIn,
    actor: Annotated[
        User,
        Depends(require_permission(Permission.SOLUTION_REGISTER, scope_kind="team")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> TeamSolutionOut:
    """Register a new solution version in the team catalog."""
    try:
        solution = SolutionRepo(session).create(
            team_id=team_id,
            solution_id=body.solution_id,
            version=body.version,
            owner_team=team_id,
            summary=body.summary,
            supported_modes=body.supported_modes,
            layers=body.layers,
            created_by=actor.id,
        )
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "solution already registered for this team and version",
        ) from exc
    return _solution_out(solution)
