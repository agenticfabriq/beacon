from uuid import UUID, uuid4

from beacon_iam.permissions import Permission, effective_permissions
from beacon_storage.models.tenancy import Membership, Role, ScopeKind


def _mem(scope_kind: ScopeKind, scope_id: UUID, role: Role, user_id: UUID) -> Membership:
    return Membership(user_id=user_id, scope_kind=scope_kind, scope_id=scope_id, role=role)


def test_team_member_can_view_team_projects() -> None:
    user_id, team_id, project_id = uuid4(), uuid4(), uuid4()
    memberships = [_mem(ScopeKind.TEAM, team_id, Role.TEAM_MEMBER, user_id)]
    perms = effective_permissions(
        user_id,
        memberships,
        target_scope_kind=ScopeKind.PROJECT,
        target_scope_id=project_id,
        target_project_team_id=team_id,
    )
    assert Permission.PROJECT_VIEW in perms
    assert Permission.PROJECT_MANAGE_MEMBERS not in perms


def test_project_owner_can_manage_project() -> None:
    user_id, team_id, project_id = uuid4(), uuid4(), uuid4()
    memberships = [_mem(ScopeKind.PROJECT, project_id, Role.PROJECT_OWNER, user_id)]
    perms = effective_permissions(
        user_id,
        memberships,
        target_scope_kind=ScopeKind.PROJECT,
        target_scope_id=project_id,
        target_project_team_id=team_id,
    )
    assert Permission.PROJECT_MANAGE_MEMBERS in perms
    assert Permission.PROJECT_RUN_EVAL in perms


def test_project_role_overrides_team_role() -> None:
    """Project owner membership adds owner permissions for that project."""
    user_id, team_id, project_id = uuid4(), uuid4(), uuid4()
    memberships = [
        _mem(ScopeKind.TEAM, team_id, Role.TEAM_MEMBER, user_id),
        _mem(ScopeKind.PROJECT, project_id, Role.PROJECT_OWNER, user_id),
    ]
    perms = effective_permissions(
        user_id,
        memberships,
        target_scope_kind=ScopeKind.PROJECT,
        target_scope_id=project_id,
        target_project_team_id=team_id,
    )
    assert Permission.PROJECT_MANAGE_MEMBERS in perms


def test_beacon_admin_can_everything() -> None:
    user_id = uuid4()
    memberships = [_mem(ScopeKind.GLOBAL, user_id, Role.BEACON_ADMIN, user_id)]
    perms = effective_permissions(
        user_id,
        memberships,
        target_scope_kind=ScopeKind.PROJECT,
        target_scope_id=uuid4(),
        target_project_team_id=uuid4(),
    )
    assert Permission.TEAM_MANAGE in perms
    assert Permission.PROJECT_MANAGE_MEMBERS in perms
