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

    memberships = MembershipRepo(session)
    requested = Role(body.role)

    # Adding YOURSELF is handled before the grant, because `grant` upserts
    # through `session.merge` and an existing row makes that an UPDATE -- which
    # the membership policy refuses for your own row, deliberately: role
    # escalation is the one write where being the subject is the point. The
    # refusal surfaced as a StaleDataError ("expected to update 1 row(s); 0
    # were matched") and nothing handles it, so re-posting your own email
    # returned a 500 where it used to return 201.
    #
    # So the two cases are separated and answered honestly rather than left to
    # an ORM error: re-adding yourself at the role you already hold is a no-op,
    # and asking for a DIFFERENT role for yourself is the escalation the policy
    # exists to stop, which deserves a reason rather than a crash.
    if target_user_id == actor.id:
        mine = next(
            (
                m
                for m in memberships.list_for_user(actor.id)
                if m.scope_kind == ScopeKind.TEAM and m.scope_id == team_id
            ),
            None,
        )
        if mine is not None:
            if Role(mine.role) is not requested:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "you cannot change your own role; another admin of this team must",
                )
            return TeamMemberOut(
                user_id=mine.user_id,
                scope_kind=mine.scope_kind,
                scope_id=mine.scope_id,
                role=mine.role,
            )

    membership = memberships.grant(
        user_id=target_user_id,
        scope_kind=ScopeKind.TEAM,
        scope_id=team_id,
        role=requested,
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
    actor: Annotated[
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

    Which is exactly why administering the TEAM is not enough to authorise it.
    The key carries the member's whole identity, not their role in this team,
    and ``effective_permissions`` counts a global membership for every team --
    so minting for someone who holds one hands over every tenant. Measured
    against the deployed app: a team admin with no global role added a global
    ``beacon_admin`` to their team by email, issued a key for them, and
    authenticated as them.

    So the rule is privilege containment, not co-membership: refuse unless
    every scope the member belongs to is one this actor administers at or
    above the member's own rank. ``may_issue_key_for`` is the single
    definition of that, shared with ``api_keys_insert`` -- and owner-privileged
    for a reason given there.
    """
    repo = MembershipRepo(session)
    memberships = repo.list_for_user(user_id)
    is_member = any(m.scope_kind == ScopeKind.TEAM and m.scope_id == team_id for m in memberships)
    if not is_member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user is not a member of this team")

    if not repo.may_issue_key_for(issuer_id=actor.id, target_id=user_id):
        # 403 and not 404: the member is visibly on the roster the caller just
        # read, so pretending they do not exist would be a lie the UI can
        # contradict on the same screen. The message says what to do instead,
        # because the honest answer is "not by you", not "not at all".
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "this member holds access outside the scopes you administer, so a key issued "
            "here would carry more privilege than you hold; they must mint their own",
        )

    key = generate_api_key(prefix=ApiConfig().api_key_prefix)
    ApiKeyRepo(session).create(user_id=user_id, key_hash=hash_api_key(key), label=body.label)
    session.commit()
    return MemberKeyOut(user_id=user_id, label=body.label, api_key=key)
