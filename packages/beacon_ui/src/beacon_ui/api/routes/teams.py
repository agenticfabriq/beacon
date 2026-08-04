from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

import sqlalchemy as sa
from beacon_iam.service.teams import TeamService
from beacon_storage.models.tenancy import Role, ScopeKind, User  # noqa: TC002
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.teams import TeamRepo
from fastapi import APIRouter, Depends, HTTPException, status
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
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> list[TeamOut]:
    """List teams the caller belongs to; a beacon_admin sees them all.

    The route always promised membership filtering; it listed every team.
    A member seeing a team they cannot read is how the UI ends up defaulting
    into a workspace that then refuses them.
    """
    memberships = MembershipRepo(session).list_for_user(user.id)
    is_admin = any(
        m.scope_kind == ScopeKind.GLOBAL and m.role == Role.BEACON_ADMIN for m in memberships
    )
    teams = TeamRepo(session).list()
    if not is_admin:
        visible = {m.scope_id for m in memberships if m.scope_kind == ScopeKind.TEAM}
        teams = [team for team in teams if team.id in visible]
    return [TeamOut.model_validate(team) for team in teams]


@router.delete(
    "/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    description="Required roles: beacon_admin. Refused unless the team is empty.",
    openapi_extra={"x-required-roles": ["beacon_admin"]},
)
def delete_team(
    team_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> None:
    """Delete a team that holds nothing.

    Undo for a mistyped creation, not a shredder: a team with benchmarks,
    runs, solutions, gold or members is refused with what stands in the way.
    Ingested data is never removed by deleting its container -- runs retire
    through invalidation, one at a time, with a reason.
    """
    memberships = MembershipRepo(session).list_for_user(user.id)
    if not any(
        m.scope_kind == ScopeKind.GLOBAL and m.role == Role.BEACON_ADMIN for m in memberships
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "deleting a team requires beacon_admin")
    team = TeamRepo(session).get(team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"team {team_id} not found")

    from beacon_storage.models.eval_items import EvalItem
    from beacon_storage.models.solutions import Solution
    from beacon_storage.models.suites import Suite
    from beacon_storage.models.tenancy import Membership

    # Grants are access, not data: they never block deletion and are revoked
    # by it. Data is what makes a team undeletable.
    holdings = {
        "benchmarks": session.scalar(
            sa.select(sa.func.count()).select_from(Suite).where(Suite.team_id == team_id)
        ),
        "solutions": session.scalar(
            sa.select(sa.func.count()).select_from(Solution).where(Solution.team_id == team_id)
        ),
        "gold items": session.scalar(
            sa.select(sa.func.count()).select_from(EvalItem).where(EvalItem.team_id == team_id)
        ),
    }
    in_the_way = {name: int(n or 0) for name, n in holdings.items() if n}
    if in_the_way:
        blockers = ", ".join(f"{n} {name}" for name, n in in_the_way.items())
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"team {team.name!r} holds data ({blockers}); a container is never deleted out from "
            "under its data -- runs retire through invalidation, benchmarks stay",
        )
    session.execute(
        sa.delete(Membership).where(
            Membership.scope_kind == ScopeKind.TEAM, Membership.scope_id == team_id
        )
    )
    session.delete(team)
    session.commit()

