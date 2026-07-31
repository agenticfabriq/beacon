"""Runs panel: filterable table and trace inspector."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import streamlit as st

from beacon_ui.dashboard.auth import client_from_state
from beacon_ui.dashboard.client import BeaconApiError
from beacon_ui.dashboard.components import format_pass_at_k, render_candidates_side_by_side
from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def _summary_value(run: dict[str, Any], key: str) -> Any:
    summary = run.get("summary")
    if not isinstance(summary, dict):
        return None
    return summary.get(key)


def _run_label(runs: list[dict[str, Any]], run_id: str) -> str:
    for run in runs:
        if str(run.get("run_id")) == run_id:
            return f"{run_id[:8]} - {run.get('mode', 'run')}"
    return run_id[:8]


def render() -> None:
    """Render the Runs tab with a filter bar, run table, and trace inspector."""
    state = _state()
    if state.current_project_id is None:
        st.info("Select a project.")
        return

    fcols = st.columns(4)
    with fcols[0]:
        mode = st.selectbox(
            "Mode",
            options=["", "EVAL", "PR_GATE", "NIGHTLY_LOO", "TRACE_PROMOTION"],
            format_func=lambda value: value or "any",
        )
    with fcols[1]:
        status = st.selectbox(
            "Status",
            options=["", "queued", "running", "completed", "failed", "cancelled"],
            format_func=lambda value: value or "any",
        )
    with fcols[2]:
        limit = st.number_input("Limit", min_value=10, max_value=500, value=50, step=10)
    with fcols[3]:
        offset = st.number_input("Offset", min_value=0, value=0, step=10)

    filters: dict[str, object] = {"limit": int(limit), "offset": int(offset)}
    if mode:
        filters["mode"] = mode
    if status:
        filters["status"] = status

    client = client_from_state()
    try:
        runs = client.list_runs(state.current_project_id, **filters)
    except BeaconApiError as exc:
        st.error(f"Failed to load runs: {exc.message}")
        return

    if not runs:
        st.caption("No runs match these filters.")
        return

    rows = [
        {
            "run_id": str(run["run_id"])[:8],
            "mode": run["mode"],
            "status": run["status"],
            "pass@1": format_pass_at_k(_summary_value(run, "pass_at_1")),
            "pass@3": format_pass_at_k(_summary_value(run, "pass_at_3")),
            "pass^3": format_pass_at_k(_summary_value(run, "pass_hat_3")),
            "median_tokens": _summary_value(run, "median_tokens"),
            "median_latency_ms": _summary_value(run, "median_latency_ms"),
            "n_items": _summary_value(run, "n_items"),
            "started_at": run.get("started_at"),
        }
        for run in runs
    ]
    st.dataframe(rows, width="stretch", hide_index=True, key="runs_table")

    run_ids = [str(run["run_id"]) for run in runs]
    selected_run_id = st.selectbox(
        "Inspect a run",
        options=run_ids,
        format_func=lambda run_id: _run_label(runs, run_id),
        key="runs_inspect_select",
    )

    if st.button("Open trace inspector", key="runs_open_inspector"):
        st.session_state["_runs_open_inspector"] = selected_run_id

    open_run_id = st.session_state.get("_runs_open_inspector")
    if not isinstance(open_run_id, str) or not open_run_id:
        return

    try:
        run = client.get_run(state.current_project_id, open_run_id)
    except BeaconApiError as exc:
        st.error(f"Failed to load run: {exc.message}")
        return

    with st.expander(f"Trace inspector - run {open_run_id[:8]}", expanded=True):
        st.json(run.get("summary", {}))
        candidates = run.get("candidates", [])
        render_candidates_side_by_side(candidates if isinstance(candidates, list) else [])
        if st.button("Close inspector", key="runs_close_inspector"):
            st.session_state["_runs_open_inspector"] = None
            st.rerun()


if __name__ == "__main__":  # pragma: no cover
    render()
