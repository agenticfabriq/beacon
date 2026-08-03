"""Role to permission mapping plus effective-permission resolution.

Two scopes only: global and team. The team is the access boundary; everything
inside it — benchmarks, runs, results — is authorized by team membership.
Project scope went with the Project table: it was a container between team and
benchmark that carried nothing of its own.
"""

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
    EVAL_VIEW = "eval.view"
    EVAL_RUN = "eval.run"
    EVAL_MANAGE = "eval.manage"
    GLOBAL_ADMIN = "global.admin"


_ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.BEACON_ADMIN: frozenset(Permission),
    Role.VIEWER: frozenset({Permission.TEAM_VIEW, Permission.EVAL_VIEW}),
    Role.TEAM_MEMBER: frozenset({Permission.TEAM_VIEW, Permission.EVAL_VIEW, Permission.EVAL_RUN}),
    Role.TEAM_ADMIN: frozenset(
        {
            Permission.TEAM_MANAGE,
            Permission.TEAM_VIEW,
            Permission.SOLUTION_REGISTER,
            Permission.EVAL_VIEW,
            Permission.EVAL_RUN,
            Permission.EVAL_MANAGE,
        }
    ),
}


def role_permissions(role: Role) -> frozenset[Permission]:
    """Return the static set of permissions granted by the given role."""
    return _ROLE_PERMISSIONS[role]


def effective_permissions(
    user_id: UUID,
    memberships: list[Membership],
    *,
    team_id: UUID,
) -> frozenset[Permission]:
    """The union of permissions a user holds within ``team_id``."""
    perms: set[Permission] = set()
    for membership in memberships:
        if membership.user_id != user_id:
            continue
        if membership.scope_kind == ScopeKind.GLOBAL or (
            membership.scope_kind == ScopeKind.TEAM and membership.scope_id == team_id
        ):
            perms.update(role_permissions(membership.role))
    return frozenset(perms)
