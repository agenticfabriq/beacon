"""Streamlit app entrypoint: the v3 layout.

Benchmark-first: the sidebar carries the benchmark selector and three questions
worth of navigation — Results, Runs, Questions — plus Benchmarks to switch
containers and Settings for everything that is configuration rather than
measurement. Team and project are resolved automatically and changed in
Settings; neither is a step on the way to a result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import streamlit as st

from beacon_ui.dashboard.auth import client_from_state, login_form
from beacon_ui.dashboard.client import BeaconApiClient, BeaconApiError
from beacon_ui.dashboard.panels import benchmarks as p_benchmarks
from beacon_ui.dashboard.panels import matrix as p_matrix
from beacon_ui.dashboard.panels import questions as p_questions
from beacon_ui.dashboard.panels import runs as p_runs
from beacon_ui.dashboard.panels import settings as p_settings
from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping

_PAGES = ["Results", "Runs", "Questions", "Benchmarks", "Settings"]


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def _first_id(rows: list[dict[str, Any]]) -> str | None:
    for row in rows:
        if row.get("id") is not None:
            return str(row["id"])
    return None


def _ensure_workspace(client: BeaconApiClient, state: DashboardState) -> None:
    """Resolve team, project and benchmark so a result is one click away."""
    if state.current_team_id is None:
        state.select_team(_first_id(client.list_teams()))
    if state.current_team_id is None:
        return
    if state.current_project_id is None:
        try:
            state.select_project(_first_id(client.list_projects(team_id=state.current_team_id)))
        except BeaconApiError:
            return
    if state.current_project_id is None or state.current_suite_id is not None:
        return
    try:
        suites = client.list_project_suites(state.current_project_id)
    except BeaconApiError:
        return
    state.select_suite(_first_id(suites))


def _sidebar(client: BeaconApiClient, state: DashboardState) -> str:
    with st.sidebar:
        st.markdown("### Beacon")
        if state.current_project_id is not None:
            try:
                suites = client.list_project_suites(state.current_project_id)
            except BeaconApiError:
                suites = []
            current = next((s for s in suites if str(s.get("id")) == state.current_suite_id), None)
            if current is not None:
                st.caption(
                    f"Benchmark: {current.get('name')} · {current.get('item_count')} questions"
                )
        page = st.radio("Navigate", options=_PAGES, key="nav_page", label_visibility="collapsed")
        return str(page)


def main() -> None:
    """Entry point that renders the Beacon dashboard."""
    st.set_page_config(page_title="Beacon", layout="wide")
    state = _state()
    if not login_form():
        return

    client = client_from_state()
    try:
        client.me()
        _ensure_workspace(client, state)
    except BeaconApiError as exc:
        st.error(f"Cannot load identity: {exc.message}")
        state.logout()
        return

    page = _sidebar(client, state)
    if page == "Results":
        p_matrix.render()
    elif page == "Runs":
        p_runs.render()
    elif page == "Questions":
        p_questions.render()
    elif page == "Benchmarks":
        p_benchmarks.render()
    else:
        p_settings.render()


if __name__ == "__main__":  # pragma: no cover
    main()
