from __future__ import annotations

from typing import Annotated

from beacon_iam.service.teams import TeamService
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.teams import TeamRepo
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_current_user, get_session
from beacon_ui.api.schemas.team import TeamIn, TeamOut

router = APIRouter(prefix="/v1/teams", tags=["teams"])


@router.post(
    "",
    response_model=TeamOut,
    status_code=status.HTTP_201_CREATED,
    description="Required roles: beacon_admin.",
    openapi_extra={"x-required-roles": ["beacon_admin"]},
)
def create_team(
    body: TeamIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> TeamOut:
    """Create a new team (beacon_admin only)."""
    team = TeamService(session).create(
        actor_id=user.id,
        name=body.name,
        description=body.description,
    )
    return TeamOut.model_validate(team)


@router.get(
    "",
    response_model=list[TeamOut],
    description="Required roles: authenticated user. Results are membership-filtered.",
    openapi_extra={"x-required-roles": ["authenticated"]},
)
def list_teams(
    user: Annotated[User, Depends(get_current_user)],  # noqa: ARG001
    session: Annotated[Session, Depends(get_session)],
) -> list[TeamOut]:
    """List teams visible to the authenticated user."""
    teams = TeamRepo(session).list()
    return [TeamOut.model_validate(team) for team in teams]
