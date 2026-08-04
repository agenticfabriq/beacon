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
from beacon_ui.api.schemas.team_member import (
    TeamMemberIn,
    TeamMemberListOut,
    TeamMemberOut,
    TeamMemberRowOut,
)

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
    """Add a user to the team in the requested role, creating them if needed.

    An email no account exists for yet is an invite, not an error: the user
    record is created now, and when that person first signs in via OIDC with
    this email the account links to their identity (UserService matches by
    email when the subject is new).
    """
    repo = UserRepo(session)
    target_user = repo.get_by_email(str(body.user_email))
    if target_user is not None and not target_user.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "user exists but is deactivated")
    if target_user is None:
        email = str(body.user_email)
        target_user = repo.create(email=email, name=email.split("@")[0])

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


@router.get(
    "/{team_id}/members",
    response_model=TeamMemberListOut,
    description="Required permission: team.manage.",
)
@requires(Permission.TEAM_MANAGE)
def list_team_members(
    team_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.TEAM_MANAGE, scope_kind="team")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> TeamMemberListOut:
    """Return the team's roster.

    Without this the members screen could only show the viewer their own
    memberships, and access could be granted but never reviewed.
    """
    memberships = MembershipRepo(session).list_for_scope(ScopeKind.TEAM, team_id)
    repo = UserRepo(session)
    rows: list[TeamMemberRowOut] = []
    for membership in memberships:
        user = repo.get(membership.user_id)
        if user is None:
            continue
        rows.append(
            TeamMemberRowOut(
                user_id=user.id, email=user.email, name=user.name, role=membership.role
            )
        )
    return TeamMemberListOut(members=rows)


@router.delete(
    "/{team_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    description="Required permission: team.manage.",
)
@requires(Permission.TEAM_MANAGE)
def remove_team_member(
    team_id: UUID,
    user_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.TEAM_MANAGE, scope_kind="team")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> None:
    """Revoke a team membership.

    Removing the last admin is refused: a team with no admin can never grant
    access again, so it would be unrecoverable through the API.
    """
    repo = MembershipRepo(session)
    memberships = repo.list_for_scope(ScopeKind.TEAM, team_id)
    target = next((m for m in memberships if m.user_id == user_id), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "membership not found")

    admins = [m for m in memberships if m.role == Role.TEAM_ADMIN]
    if target.role == Role.TEAM_ADMIN and len(admins) == 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "cannot remove the last team admin; grant another admin first",
        )

    repo.revoke(user_id=user_id, scope_kind=ScopeKind.TEAM, scope_id=team_id)
    session.commit()
