from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_storage.models.tenancy import Project, Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.teams import TeamRepo
from sqlalchemy.exc import IntegrityError

from beacon_iam.errors import AuthorizationError, ConflictError, NotFoundError
from beacon_iam.permissions import Permission, effective_permissions

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class ProjectService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepo(session)
        self.teams = TeamRepo(session)
        self.memberships = MembershipRepo(session)

    def create(
        self,
        *,
        actor_id: UUID,
        team_id: UUID,
        name: str,
        description: str | None = None,
    ) -> Project:
        """Create a team-scoped project and grant the actor PROJECT_OWNER on it."""
        team = self.teams.get(team_id)
        if team is None:
            raise NotFoundError(f"team {team_id} not found")

        actor_memberships = self.memberships.list_for_user(actor_id)
        permissions = effective_permissions(
            actor_id,
            actor_memberships,
            target_scope_kind=ScopeKind.TEAM,
            target_scope_id=team_id,
        )
        if Permission.PROJECT_CREATE not in permissions:
            raise AuthorizationError("not authorized to create projects in this team")

        try:
            project = self.projects.create(
                team_id=team_id,
                name=name,
                description=description,
                created_by=actor_id,
            )
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError(f"project name '{name}' already exists in this team") from exc

        self.memberships.grant(
            user_id=actor_id,
            scope_kind=ScopeKind.PROJECT,
            scope_id=project.id,
            role=Role.PROJECT_OWNER,
        )
        return project

    def list_for_team(
        self,
        *,
        actor_id: UUID,
        team_id: UUID,
        include_archived: bool = False,
    ) -> list[Project]:
        """List projects in a team that the actor can view (requires project.view)."""
        actor_memberships = self.memberships.list_for_user(actor_id)
        permissions = effective_permissions(
            actor_id,
            actor_memberships,
            target_scope_kind=ScopeKind.TEAM,
            target_scope_id=team_id,
        )
        if Permission.PROJECT_VIEW not in permissions:
            raise AuthorizationError("not authorized to view projects in this team")
        return self.projects.list_for_team(team_id, include_archived=include_archived)
