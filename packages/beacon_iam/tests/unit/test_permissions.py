"""What each role may do inside a team.

Two scopes only: global and team. The team is the access boundary; a benchmark
and its runs are authorized by team membership, and there is no narrower scope
to escalate into or hide behind.
"""

from uuid import UUID, uuid4

from beacon_iam.permissions import Permission, effective_permissions
from beacon_storage.models.tenancy import Membership, Role, ScopeKind


def _mem(scope_kind: ScopeKind, scope_id: UUID, role: Role, user_id: UUID) -> Membership:
    return Membership(user_id=user_id, scope_kind=scope_kind, scope_id=scope_id, role=role)


def _perms(role: Role, *, user_id: UUID, team_id: UUID) -> frozenset[Permission]:
    return effective_permissions(
        user_id, [_mem(ScopeKind.TEAM, team_id, role, user_id)], team_id=team_id
    )


def test_a_team_member_can_view_and_run_but_not_manage() -> None:
    user_id, team_id = uuid4(), uuid4()

    perms = _perms(Role.TEAM_MEMBER, user_id=user_id, team_id=team_id)

    assert Permission.EVAL_VIEW in perms
    assert Permission.EVAL_RUN in perms
    assert Permission.EVAL_MANAGE not in perms
    assert Permission.TEAM_MANAGE not in perms


def test_a_team_admin_manages_evals_and_the_roster() -> None:
    user_id, team_id = uuid4(), uuid4()

    perms = _perms(Role.TEAM_ADMIN, user_id=user_id, team_id=team_id)

    assert Permission.EVAL_MANAGE in perms
    assert Permission.TEAM_MANAGE in perms
    assert Permission.SOLUTION_REGISTER in perms


def test_a_viewer_only_views() -> None:
    user_id, team_id = uuid4(), uuid4()

    perms = _perms(Role.VIEWER, user_id=user_id, team_id=team_id)

    assert perms == frozenset({Permission.TEAM_VIEW, Permission.EVAL_VIEW})


def test_membership_in_another_team_grants_nothing_here() -> None:
    user_id, mine, other = uuid4(), uuid4(), uuid4()
    memberships = [_mem(ScopeKind.TEAM, other, Role.TEAM_ADMIN, user_id)]

    assert effective_permissions(user_id, memberships, team_id=mine) == frozenset()


def test_someone_elses_membership_grants_me_nothing() -> None:
    me, them, team_id = uuid4(), uuid4(), uuid4()
    memberships = [_mem(ScopeKind.TEAM, team_id, Role.TEAM_ADMIN, them)]

    assert effective_permissions(me, memberships, team_id=team_id) == frozenset()


def test_a_global_admin_holds_everything_in_every_team() -> None:
    user_id, team_id = uuid4(), uuid4()
    memberships = [_mem(ScopeKind.GLOBAL, uuid4(), Role.BEACON_ADMIN, user_id)]

    perms = effective_permissions(user_id, memberships, team_id=team_id)

    assert perms == frozenset(Permission)
