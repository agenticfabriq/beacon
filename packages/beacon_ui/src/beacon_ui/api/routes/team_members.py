from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

from beacon_iam.permissions import Permission
from beacon_storage.models.tenancy import Role, ScopeKind, User  # noqa: TC002
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.users import UserRepo
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.team_member import TeamMemberIn, TeamMemberOut

router = APIRouter(prefix="/v1/teams", tags=["team-members"])


@router.post(
    "/{team_id}/members",
    response_model=TeamMemberOut,
    status_code=status.HTTP_201_CREATED,
    description="Required permission: team.manage.",
)
@requires(Permission.TEAM_MANAGE)
def add_team_member(
    team_id: UUID,
    body: TeamMemberIn,
    actor: Annotated[
        User,
        Depends(require_permission(Permission.TEAM_MANAGE, scope_kind="team")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> TeamMemberOut:
    """Add a user to the team in the requested role."""
    target_user = UserRepo(session).get_by_email(str(body.user_email))
    if target_user is None or not target_user.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")

    membership = MembershipRepo(session).grant(
        user_id=target_user.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=team_id,
        role=Role(body.role),
        granted_by=actor.id,
    )
    return TeamMemberOut(
        user_id=membership.user_id,
        scope_kind=membership.scope_kind,
        scope_id=membership.scope_id,
        role=membership.role,
    )
