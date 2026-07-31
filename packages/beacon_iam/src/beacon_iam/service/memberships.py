from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_storage.models.tenancy import Membership, Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.users import UserRepo

from beacon_iam.errors import AuthorizationError, NotFoundError
from beacon_iam.permissions import Permission, effective_permissions

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class MembershipService:
    def __init__(self, session: Session) -> None:
        self.memberships = MembershipRepo(session)
        self.users = UserRepo(session)
        self.projects = ProjectRepo(session)

    def grant_project_membership(
        self,
        *,
        actor_id: UUID,
        project_id: UUID,
        target_user_id: UUID,
        role: Role,
    ) -> Membership:
        """Grant a project-scoped role to a user, gated by project.manage_members."""
        project = self.projects.get(project_id)
        if project is None:
            raise NotFoundError(f"project {project_id} not found")

        actor_memberships = self.memberships.list_for_user(actor_id)
        permissions = effective_permissions(
            actor_id,
            actor_memberships,
            target_scope_kind=ScopeKind.PROJECT,
            target_scope_id=project_id,
            target_project_team_id=project.team_id,
        )
        if Permission.PROJECT_MANAGE_MEMBERS not in permissions:
            raise AuthorizationError("not authorized to manage project members")
        if self.users.get(target_user_id) is None:
            raise NotFoundError(f"user {target_user_id} not found")

        return self.memberships.grant(
            user_id=target_user_id,
            scope_kind=ScopeKind.PROJECT,
            scope_id=project_id,
            role=role,
            granted_by=actor_id,
        )
