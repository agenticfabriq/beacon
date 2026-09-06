from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

from beacon_iam.auth.api_key import generate_api_key, hash_api_key
from beacon_iam.permissions import Permission
from beacon_storage.models.tenancy import Role, ScopeKind, User  # noqa: TC002
from beacon_storage.repository.api_keys import ApiKeyRepo
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.users import UserRepo
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.config import ApiConfig
from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.team_member import (
    MemberKeyIn,
    MemberKeyOut,
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
    email = str(body.user_email)
    # `find_for_invite`, not `get_by_email`: the ordinary read is bounded by
    # `users_read`, so an invitee who is not yet a co-member is invisible and
    # the branch below would create a duplicate account -- a unique-constraint
    # violation no handler catches, so a 500. See that method for why the
    # lookup is owner-privileged and why it returns two fields rather than a
    # User.
    existing = repo.find_for_invite(email)
    if existing is not None and not existing.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "user exists but is deactivated")
    target_user_id = (
        existing.id
        if existing is not None
        else repo.create(email=email, name=email.split("@")[0]).id
    )

    membership = MembershipRepo(session).grant(
        user_id=target_user_id,
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


@router.post(
    "/{team_id}/members/{user_id}/api-keys",
    response_model=MemberKeyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Issue an API key for a team member",
    description="Required permission: team.manage.",
)
@requires(Permission.TEAM_MANAGE)
def issue_member_key(
    team_id: UUID,
    user_id: UUID,
    body: MemberKeyIn,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.TEAM_MANAGE, scope_kind="team")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> MemberKeyOut:
    """Mint a key for a member and return it once.

    This is how an invited person gets their first credential: the admin
    issues a key from the roster and hands it over out of band. The key
    authenticates the member themselves -- same thing they would mint from
    their own Settings once signed in.
    """
    memberships = MembershipRepo(session).list_for_user(user_id)
    is_member = any(m.scope_kind == ScopeKind.TEAM and m.scope_id == team_id for m in memberships)
    if not is_member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user is not a member of this team")

    key = generate_api_key(prefix=ApiConfig().api_key_prefix)
    ApiKeyRepo(session).create(user_id=user_id, key_hash=hash_api_key(key), label=body.label)
    session.commit()
    return MemberKeyOut(user_id=user_id, label=body.label, api_key=key)
