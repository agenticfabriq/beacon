"""Role to permission mapping plus effective-permission resolution."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from beacon_storage.models.tenancy import Membership, Role, ScopeKind

if TYPE_CHECKING:
    from uuid import UUID


class Permission(StrEnum):
    TEAM_MANAGE = "team.manage"
    TEAM_VIEW = "team.view"
    SOLUTION_REGISTER = "team.solution.register"
    SECRET_MANAGE = "team.secret.manage"  # noqa: S105
    PROJECT_CREATE = "project.create"
    PROJECT_MANAGE = "project.manage"
    PROJECT_MANAGE_MEMBERS = "project.manage_members"
    PROJECT_VIEW = "project.view"
    PROJECT_RUN_EVAL = "project.run_eval"
    PROJECT_PROMOTE_ITEM = "project.promote_item"
    GLOBAL_ADMIN = "global.admin"


_ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.BEACON_ADMIN: frozenset(Permission),
    Role.VIEWER: frozenset(
        {
            Permission.TEAM_VIEW,
            Permission.PROJECT_VIEW,
        }
    ),
    Role.TEAM_ADMIN: frozenset(
        {
            Permission.TEAM_MANAGE,
            Permission.TEAM_VIEW,
            Permission.SOLUTION_REGISTER,
            Permission.SECRET_MANAGE,
            Permission.PROJECT_CREATE,
            Permission.PROJECT_MANAGE,
            Permission.PROJECT_MANAGE_MEMBERS,
            Permission.PROJECT_VIEW,
            Permission.PROJECT_RUN_EVAL,
            Permission.PROJECT_PROMOTE_ITEM,
        }
    ),
    Role.TEAM_MEMBER: frozenset(
        {
            Permission.TEAM_VIEW,
            Permission.PROJECT_CREATE,
            Permission.PROJECT_VIEW,
        }
    ),
    Role.PROJECT_OWNER: frozenset(
        {
            Permission.PROJECT_MANAGE,
            Permission.PROJECT_MANAGE_MEMBERS,
            Permission.PROJECT_VIEW,
            Permission.PROJECT_RUN_EVAL,
            Permission.PROJECT_PROMOTE_ITEM,
        }
    ),
    Role.PROJECT_CONTRIBUTOR: frozenset(
        {
            Permission.PROJECT_VIEW,
            Permission.PROJECT_RUN_EVAL,
            Permission.PROJECT_PROMOTE_ITEM,
        }
    ),
    Role.PROJECT_VIEWER: frozenset({Permission.PROJECT_VIEW}),
}


def role_permissions(role: Role) -> frozenset[Permission]:
    """Return the static set of permissions granted by the given role."""
    return _ROLE_PERMISSIONS[role]


def effective_permissions(
    user_id: UUID,
    memberships: list[Membership],
    *,
    target_scope_kind: ScopeKind,
    target_scope_id: UUID,
    target_project_team_id: UUID | None = None,
) -> frozenset[Permission]:
    """Compute the union of permissions a user has at the target scope."""
    perms: set[Permission] = set()
    for membership in memberships:
        if membership.user_id != user_id:
            continue
        if membership.scope_kind == ScopeKind.GLOBAL:
            perms.update(role_permissions(membership.role))
        elif membership.scope_kind == ScopeKind.TEAM:
            if (
                target_scope_kind == ScopeKind.TEAM
                and membership.scope_id == target_scope_id
                or target_scope_kind == ScopeKind.PROJECT
                and membership.scope_id == target_project_team_id
            ):
                perms.update(role_permissions(membership.role))
        elif (
            membership.scope_kind == ScopeKind.PROJECT
            and target_scope_kind == ScopeKind.PROJECT
            and membership.scope_id == target_scope_id
        ):
            perms.update(role_permissions(membership.role))
    return frozenset(perms)
