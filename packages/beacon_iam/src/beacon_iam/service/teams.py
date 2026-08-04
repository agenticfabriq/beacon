from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_storage.models.tenancy import (
    Role,
    ScopeKind,
    Team,  # noqa: TC002
)
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.teams import TeamRepo
from sqlalchemy.exc import IntegrityError

from beacon_iam.errors import AuthorizationError, ConflictError
from beacon_iam.permissions import Permission, effective_permissions

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class TeamService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.teams = TeamRepo(session)
        self.memberships = MembershipRepo(session)

    def create(self, *, actor_id: UUID, name: str, description: str | None = None) -> Team:
        """Create a new team; requires the actor to hold global.admin."""
        actor_memberships = self.memberships.list_for_user(actor_id)
        # Any team id works here: GLOBAL_ADMIN comes only from a global-scope
        # membership, which effective_permissions honours regardless of team.
        permissions = effective_permissions(actor_id, actor_memberships, team_id=actor_id)
        if Permission.GLOBAL_ADMIN not in permissions:
            raise AuthorizationError("create team requires beacon_admin")

        try:
            team = self.teams.create(name=name, description=description)
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError(f"team name '{name}' already exists") from exc

        # The creator owns what they created: without this the team is born
        # ownerless -- an empty roster only global admins can even see.
        self.memberships.grant(
            user_id=actor_id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team.id,
            role=Role.TEAM_ADMIN,
            granted_by=actor_id,
        )
        return team
