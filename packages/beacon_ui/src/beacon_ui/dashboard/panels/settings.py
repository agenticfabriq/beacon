"""Project settings panel."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import streamlit as st

from beacon_ui.dashboard.auth import client_from_state
from beacon_ui.dashboard.client import BeaconApiError
from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def selected_project(projects: list[dict[str, Any]], project_id: str) -> dict[str, Any] | None:
    """Return the project dict matching project_id from the list, or None."""
    for project in projects:
        if str(project.get("id")) == project_id:
            return project
    return None


def _policy_text(project: dict[str, Any]) -> str:
    policy = project.get("gate_policy")
    return json.dumps(policy if isinstance(policy, dict) else {}, indent=2, sort_keys=True)


def render() -> None:
    """Render the project Settings tab with baseline, gate policy, and archive."""
    state = _state()
    if state.current_team_id is None:
        st.info("Select a team.")
        return
    if state.current_project_id is None:
        st.info("Select a project.")
        return

    client = client_from_state()
    try:
        projects = client.list_projects(team_id=state.current_team_id)
        runs = client.list_runs(state.current_project_id, limit=50, status="success")
    except BeaconApiError as exc:
        st.error(exc.message)
        return

    project = selected_project(projects, state.current_project_id)
    if project is None:
        st.error("Selected project is not visible to this account.")
        return

    st.subheader("Baseline run")
    st.caption("Pinned baseline drives PR_GATE comparisons and the Overview delta tile.")
    options = [""] + [str(run["run_id"]) for run in runs]
    current = str(project.get("baseline_run_id") or "")
    baseline = st.selectbox(
        "Baseline run ID",
        options=options,
        format_func=lambda run_id: run_id[:8] if run_id else "(none)",
        index=options.index(current) if current in options else 0,
        key="settings_baseline_select",
    )
    if st.button("Save baseline", key="settings_baseline_btn"):
        try:
            client.patch_project_settings(
                state.current_project_id,
                baseline_run_id=baseline or None,
            )
        except BeaconApiError as exc:
            st.error(exc.message)
            return
        st.success("Baseline saved.")
        st.rerun()

    st.divider()
    st.subheader("Gate policy")
    policy_text = st.text_area(
        "Policy JSON",
        value=_policy_text(project),
        height=200,
        key="settings_policy_text",
    )
    if st.button("Save policy", key="settings_policy_btn"):
        try:
            policy = json.loads(policy_text)
            if not isinstance(policy, dict):
                st.error("Policy JSON must be an object.")
                return
            client.patch_project_settings(state.current_project_id, gate_policy=policy)
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")
            return
        except BeaconApiError as exc:
            st.error(exc.message)
            return
        st.success("Policy saved.")
        st.rerun()

    st.divider()
    st.subheader("Archive")
    st.caption("Archived projects are hidden from active project lists.")
    if st.button(
        "Archive project",
        type="secondary",
        disabled=bool(project.get("archived_at")),
        key="settings_archive_btn",
    ):
        try:
            client._delete(f"/v1/projects/{state.current_project_id}")  # noqa: SLF001
        except BeaconApiError as exc:
            st.error(exc.message)
            return
        st.success("Project archived.")
        st.rerun()


if __name__ == "__main__":  # pragma: no cover
    render()
