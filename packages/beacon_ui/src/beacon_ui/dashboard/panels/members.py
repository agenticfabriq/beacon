"""Project members panel."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import streamlit as st

from beacon_ui.dashboard.auth import client_from_state
from beacon_ui.dashboard.client import BeaconApiError
from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def visible_memberships(
    me: dict[str, Any],
    *,
    team_id: str,
    project_id: str,
) -> list[dict[str, Any]]:
    """Return memberships scoped to the given team or project for the current user."""
    memberships = me.get("memberships", [])
    if not isinstance(memberships, list):
        return []

    rows: list[dict[str, Any]] = []
    for membership in memberships:
        if not isinstance(membership, dict):
            continue
        scope_kind = str(membership.get("scope_kind"))
        scope_id = str(membership.get("scope_id"))
        if (scope_kind == "team" and scope_id == team_id) or (
            scope_kind == "project" and scope_id == project_id
        ):
            rows.append(
                {
                    "scope_kind": scope_kind,
                    "scope_id": scope_id,
                    "role": membership.get("role"),
                }
            )
    return rows


def render() -> None:
    """Render the Members tab with visible memberships and project-role grants."""
    state = _state()
    if state.current_team_id is None:
        st.info("Select a team.")
        return
    if state.current_project_id is None:
        st.info("Select a project.")
        return

    client = client_from_state()
    try:
        me = client.me()
    except BeaconApiError as exc:
        st.error(exc.message)
        return

    user = me.get("user", {})
    st.subheader("Visible memberships")
    if isinstance(user, dict):
        st.caption(f"Signed in as {user.get('email', 'unknown')}.")

    memberships = visible_memberships(
        me,
        team_id=state.current_team_id,
        project_id=state.current_project_id,
    )
    if memberships:
        st.dataframe(memberships, width="stretch", hide_index=True)
    else:
        st.caption("No project-scoped membership is visible. Team membership may still apply.")

    st.divider()
    st.subheader("Grant project role")
    st.caption("The current API grants project roles by user ID.")
    user_id = st.text_input("User ID", key="members_invite_user_id")
    role = st.selectbox(
        "Role",
        options=["project_owner", "project_contributor", "project_viewer"],
        key="members_invite_role",
    )
    if st.button("Grant role", type="primary", disabled=not user_id.strip()):
        try:
            client._post(  # noqa: SLF001 - no typed helper exists for this legacy endpoint yet.
                f"/v1/projects/{state.current_project_id}/members",
                {"user_id": user_id.strip(), "role": role},
            )
        except BeaconApiError as exc:
            st.error(exc.message)
            return
        st.success("Project role granted.")
        st.rerun()


if __name__ == "__main__":  # pragma: no cover
    render()
