"""Top navigation for the Streamlit dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import streamlit as st

from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def render_top_nav(me: dict[str, Any], teams: list[dict[str, Any]]) -> None:
    """Render the dashboard top bar with team picker and logout."""
    state = DashboardState(cast("MutableMapping[str, object]", st.session_state))
    cols = st.columns([3, 3, 2, 1])
    with cols[0]:
        user = me.get("user", {})
        email = user.get("email", "unknown")
        st.markdown(f"**Beacon** - {email}")
    with cols[1]:
        team_options = {str(team["id"]): str(team["name"]) for team in teams if "id" in team}
        if not team_options:
            st.info("You are not in any teams yet.")
        else:
            option_ids = list(team_options)
            current = state.current_team_id or option_ids[0]
            index = option_ids.index(current) if current in team_options else 0
            chosen = st.selectbox(
                "Team",
                options=option_ids,
                index=index,
                format_func=lambda team_id: team_options[team_id],
                key="top_nav_team_select",
            )
            if chosen != state.current_team_id:
                state.select_team(chosen)
                st.rerun()
    with cols[2]:
        if st.button("Settings", key="top_nav_settings"):
            state.bag["nav_view"] = "settings"
            st.rerun()
    with cols[3]:
        if st.button("Logout", key="top_nav_logout"):
            state.logout()
            st.rerun()
