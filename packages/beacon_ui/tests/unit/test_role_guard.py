from __future__ import annotations

from beacon_ui.dashboard.components.role_guard import has_role_in


def test_role_in_returns_true_when_role_present() -> None:
    memberships = [
        {"scope_kind": "team", "scope_id": "t1", "role": "team_admin"},
        {"scope_kind": "project", "scope_id": "p1", "role": "project_owner"},
    ]

    assert (
        has_role_in(
            memberships,
            scope_kind="team",
            scope_id="t1",
            roles=["team_admin"],
        )
        is True
    )


def test_role_in_returns_false_when_role_absent() -> None:
    memberships = [{"scope_kind": "team", "scope_id": "t1", "role": "team_member"}]

    assert (
        has_role_in(
            memberships,
            scope_kind="team",
            scope_id="t1",
            roles=["team_admin"],
        )
        is False
    )


def test_role_in_returns_false_when_scope_differs() -> None:
    memberships = [{"scope_kind": "team", "scope_id": "t1", "role": "team_admin"}]

    assert (
        has_role_in(
            memberships,
            scope_kind="team",
            scope_id="t2",
            roles=["team_admin"],
        )
        is False
    )


def test_role_in_global_admin_grants_everything() -> None:
    memberships = [{"scope_kind": "global", "scope_id": "x", "role": "beacon_admin"}]

    assert (
        has_role_in(
            memberships,
            scope_kind="project",
            scope_id="anything",
            roles=["project_owner"],
        )
        is True
    )
