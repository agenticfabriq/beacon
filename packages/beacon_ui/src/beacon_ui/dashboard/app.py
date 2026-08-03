"""Streamlit app entrypoint for the Beacon dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import streamlit as st

from beacon_ui.dashboard.auth import client_from_state, login_form
from beacon_ui.dashboard.client import BeaconApiClient, BeaconApiError
from beacon_ui.dashboard.components import render_top_nav
from beacon_ui.dashboard.global_views import leaderboards as g_leaderboards
from beacon_ui.dashboard.panels import (
    attribution as p_attribution,
)
from beacon_ui.dashboard.panels import (
    cost_accuracy as p_cost_accuracy,
)
from beacon_ui.dashboard.panels import (
    members as p_members,
)
from beacon_ui.dashboard.panels import (
    overview as p_overview,
)
from beacon_ui.dashboard.panels import (
    runs as p_runs,
)
from beacon_ui.dashboard.panels import (
    settings as p_settings,
)
from beacon_ui.dashboard.panels import (
    solutions as p_solutions,
)
from beacon_ui.dashboard.panels import (
    suites as p_suites,
)
from beacon_ui.dashboard.state import DashboardState
from beacon_ui.dashboard.team_views import (
    members as t_members,
)
from beacon_ui.dashboard.team_views import (
    solutions_catalog as t_solutions,
)

if TYPE_CHECKING:
    from collections.abc import MutableMapping


_SECTION_LABELS = {
    "project": "Project workspace",
    "team": "Team-level views",
    "global": "Global leaderboards",
}


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def _sidebar(client: BeaconApiClient, state: DashboardState) -> None:
    with st.sidebar:
        st.markdown("### Navigate")
        view = st.radio(
            "Section",
            options=["project", "team", "global"],
            format_func=lambda value: _SECTION_LABELS[value],
            key="nav_section",
        )
        state.bag["nav_view"] = view

        if view == "project":
            _project_selector(client, state)
            return

        if view == "team":
            team_view = st.radio(
                "Team view",
                options=["solutions_catalog", "members"],
                format_func=lambda value: {
                    "solutions_catalog": "Solutions catalog",
                    "members": "Members",
                }[value],
                key="nav_team_view",
            )
            state.bag["team_subview"] = team_view


def _project_selector(client: BeaconApiClient, state: DashboardState) -> None:
    if state.current_team_id is None:
        st.caption("Select a team first.")
        return

    try:
        projects = client.list_projects(team_id=state.current_team_id)
    except BeaconApiError:
        projects = []

    if not projects:
        st.caption("No projects in this team.")
        return

    options = {str(project["id"]): str(project["name"]) for project in projects if "id" in project}
    if not options:
        st.caption("No projects in this team.")
        return

    current = state.current_project_id or next(iter(options))
    option_ids = list(options)
    chosen = st.selectbox(
        "Project",
        options=option_ids,
        index=option_ids.index(current) if current in options else 0,
        format_func=lambda project_id: options[project_id],
        key="nav_project_select",
    )
    if chosen != state.current_project_id:
        state.select_project(chosen)
        st.rerun()


def _render_project_workspace() -> None:
    tabs = st.tabs(
        [
            "Overview",
            "Runs",
            "Solutions",
            "Suites",
            "Attribution",
            "Cost/Accuracy",
            "Members",
            "Settings",
        ]
    )
    with tabs[0]:
        p_overview.render()
    with tabs[1]:
        p_runs.render()
    with tabs[2]:
        p_solutions.render()
    with tabs[3]:
        p_suites.render()
    with tabs[4]:
        p_attribution.render()
    with tabs[5]:
        p_cost_accuracy.render()
    with tabs[6]:
        p_members.render()
    with tabs[7]:
        p_settings.render()


def _render_team_view(state: DashboardState) -> None:
    subview = state.bag.get("team_subview", "solutions_catalog")
    if subview == "solutions_catalog":
        t_solutions.render()
    elif subview == "members":
        t_members.render()
    else:
        st.error(f"Unknown team view: {subview}")


def _first_team_id(teams: list[dict[str, Any]]) -> str | None:
    for team in teams:
        team_id = team.get("id")
        if team_id is not None:
            return str(team_id)
    return None


def main() -> None:
    """Entry point that renders the Beacon Streamlit dashboard."""
    st.set_page_config(page_title="Beacon", layout="wide")
    state = _state()
    if not login_form():
        return

    client = client_from_state()
    try:
        me = client.me()
        teams = client.list_teams()
    except BeaconApiError as exc:
        st.error(f"Cannot load identity: {exc.message}")
        state.logout()
        return

    if state.current_team_id is None:
        state.select_team(_first_team_id(teams))

    render_top_nav(me, teams)
    _sidebar(client, state)

    view = state.bag.get("nav_view", "project")
    if view == "project":
        if state.current_project_id is None:
            st.info("Pick a project from the sidebar to begin.")
            return
        _render_project_workspace()
        return

    if view == "team":
        _render_team_view(state)
        return

    if view == "global":
        g_leaderboards.render()


if __name__ == "__main__":  # pragma: no cover
    main()
