"""Overview panel: summary tiles, recent runs, baseline diff, alerts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import streamlit as st

from beacon_ui.dashboard.auth import client_from_state
from beacon_ui.dashboard.client import BeaconApiError
from beacon_ui.dashboard.components import format_delta, format_pass_at_k, kpi_tile
from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def _selected_project(projects: list[dict[str, Any]], project_id: str) -> dict[str, Any] | None:
    for project in projects:
        if str(project.get("id")) == project_id:
            return project
    return None


def _latest_completed_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    completed_statuses = {"completed", "success"}
    return next((run for run in runs if run.get("status") in completed_statuses), None)


def render() -> None:
    """Render the Overview tab with project KPIs, recent runs, and alerts."""
    state = _state()
    if state.current_team_id is None:
        st.info("Select a team from the navigation.")
        return
    if state.current_project_id is None:
        st.info("Select a project from the navigation.")
        return

    client = client_from_state()
    try:
        projects = client.list_projects(team_id=state.current_team_id)
        runs = client.list_runs(state.current_project_id, limit=10)
    except BeaconApiError as exc:
        st.error(f"Failed to load project: {exc.message}")
        return

    project = _selected_project(projects, state.current_project_id)
    if project is None:
        st.error("Selected project is not visible to this account.")
        return

    st.subheader(f"Project: {project['name']}")
    st.caption(project.get("description") or "n/a")

    latest = _latest_completed_run(runs)
    latest_summary = latest.get("summary", {}) if latest else {}
    cols = st.columns(4)
    with cols[0]:
        kpi_tile("pass@1", format_pass_at_k(latest_summary.get("pass_at_1")))
    with cols[1]:
        kpi_tile("pass@3", format_pass_at_k(latest_summary.get("pass_at_3")))
    with cols[2]:
        kpi_tile(
            "pass^3",
            format_pass_at_k(latest_summary.get("pass_hat_3")),
            help="Consistency: all-of-3 succeed",
        )
    with cols[3]:
        kpi_tile("# runs (recent)", str(len(runs)))

    baseline_id = project.get("baseline_run_id")
    if baseline_id and latest:
        try:
            baseline = client.get_run(state.current_project_id, str(baseline_id))
        except BeaconApiError:
            st.caption("Baseline run not accessible.")
        else:
            baseline_summary = baseline.get("summary", {})
            latest_pass_at_3 = latest_summary.get("pass_at_3") or 0.0
            baseline_pass_at_3 = baseline_summary.get("pass_at_3") or 0.0
            delta_3 = latest_pass_at_3 - baseline_pass_at_3
            st.metric(
                "Delta pass@3 vs baseline",
                format_pass_at_k(delta_3),
                delta=format_delta(delta_3),
            )
    elif "baseline_run_id" in project:
        st.info("No baseline pinned. Set one in Settings > baseline.")

    st.subheader("Recent runs")
    if not runs:
        st.caption("No runs yet.")
    else:
        st.dataframe(
            [
                {
                    "run_id": str(run["run_id"])[:8],
                    "mode": run["mode"],
                    "status": run["status"],
                    "pass@3": format_pass_at_k(run.get("summary", {}).get("pass_at_3")),
                    "n_items": run.get("summary", {}).get("n_items"),
                    "started_at": run.get("started_at"),
                }
                for run in runs
            ],
            width="stretch",
            hide_index=True,
        )

    st.subheader("Alerts")
    failed = [run for run in runs if run.get("status") in {"error", "failed"}]
    if failed:
        st.warning(f"{len(failed)} run(s) failed recently.")
    else:
        st.success("No recent failures.")


if __name__ == "__main__":  # pragma: no cover
    render()
