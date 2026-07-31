from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_storage.models.tenancy import ScopeKind, Team
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
        permissions = effective_permissions(
            actor_id,
            actor_memberships,
            target_scope_kind=ScopeKind.TEAM,
            target_scope_id=actor_id,
        )
        if Permission.GLOBAL_ADMIN not in permissions:
            raise AuthorizationError("create team requires beacon_admin")

        try:
            return self.teams.create(name=name, description=description)
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError(f"team name '{name}' already exists") from exc
