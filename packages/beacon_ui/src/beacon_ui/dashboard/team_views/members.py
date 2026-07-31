"""Team members view."""

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


def _team_memberships(me: dict[str, Any], *, team_id: str) -> list[dict[str, Any]]:
    memberships = me.get("memberships", [])
    if not isinstance(memberships, list):
        return []

    rows: list[dict[str, Any]] = []
    for membership in memberships:
        if not isinstance(membership, dict):
            continue
        if str(membership.get("scope_kind")) != "team":
            continue
        if str(membership.get("scope_id")) != team_id:
            continue
        rows.append(
            {
                "scope_id": membership.get("scope_id"),
                "role": membership.get("role"),
            }
        )
    return rows


def render() -> None:
    """Render the team Members view and a form to invite users."""
    state = _state()
    if state.current_team_id is None:
        st.info("Select a team.")
        return

    client = client_from_state()
    try:
        me = client.me()
    except BeaconApiError as exc:
        st.error(f"Failed to load memberships: {exc.message}")
        return

    st.subheader("Team members")
    user = me.get("user", {})
    if isinstance(user, dict):
        st.caption(f"Signed in as {user.get('email', 'unknown')}.")

    rows = _team_memberships(me, team_id=state.current_team_id)
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.caption("No team membership is visible for this session.")

    st.divider()
    st.subheader("Invite member")
    email = st.text_input("Email", key="team_members_invite_email")
    role = st.selectbox(
        "Role",
        options=["team_member", "team_admin"],
        key="team_members_invite_role",
    )
    if st.button("Invite", type="primary", disabled=not email.strip()):
        try:
            client.add_team_member(
                state.current_team_id,
                user_email=email.strip(),
                role=role,
            )
        except BeaconApiError as exc:
            st.error(exc.message)
            return
        st.success("Team member added.")
        st.rerun()


if __name__ == "__main__":  # pragma: no cover
    render()
