"""Team members view."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import streamlit as st

from beacon_ui.dashboard.auth import client_from_state
from beacon_ui.dashboard.client import BeaconApiError
from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def render() -> None:
    """Render the team Members view and a form to invite users."""
    state = _state()
    if state.current_team_id is None:
        st.info("Select a team.")
        return

    client = client_from_state()
    st.subheader("Team members")
    try:
        # The real roster. This view used to fall back to /v1/me and display
        # the viewer's own memberships as though they were the team's.
        members = client.list_team_members(state.current_team_id)
    except BeaconApiError as exc:
        st.error(f"Failed to load the roster: {exc.message}")
        return

    if members:
        st.dataframe(
            [
                {"email": m.get("email"), "name": m.get("name"), "role": m.get("role")}
                for m in members
            ],
            width="stretch",
            hide_index=True,
        )
        removable = [m for m in members if isinstance(m.get("user_id"), str)]
        target = st.selectbox(
            "Remove a member",
            options=[str(m["user_id"]) for m in removable],
            format_func=lambda uid: next(
                str(m.get("email")) for m in removable if str(m["user_id"]) == uid
            ),
            key="team_members_remove_select",
        )
        if st.button("Remove", key="team_members_remove_btn"):
            try:
                client.remove_team_member(state.current_team_id, target)
            except BeaconApiError as exc:
                # Removing the last admin is refused so a team stays recoverable.
                st.error(exc.message)
                return
            st.rerun()
    else:
        st.caption("No members.")

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
