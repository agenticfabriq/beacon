"""Runs panel: filterable table, per-item drill-down, and invalidation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import streamlit as st

from beacon_ui.dashboard.auth import client_from_state
from beacon_ui.dashboard.client import BeaconApiError
from beacon_ui.dashboard.components import format_pass_at_k
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
            options=["", "EVAL", "NIGHTLY_LOO"],
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
    show_invalidated = st.checkbox(
        "Show invalidated runs",
        value=False,
        help="Retired runs keep their results but leave every aggregate.",
    )

    filters: dict[str, object] = {"limit": int(limit), "offset": int(offset)}
    # Benchmark-scoped: runs from other suites are a different comparison.
    if state.current_suite_id is not None:
        filters["suite_id"] = state.current_suite_id
    if mode:
        filters["mode"] = mode
    if status:
        filters["status"] = status
    if show_invalidated:
        filters["include_invalidated"] = True

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
            "model": run.get("model_id") or "",
            "config": run.get("config_label") or "",
            # Which ablation arm produced this run, for NIGHTLY_LOO sweeps.
            "arm": run.get("sweep_arm") or "",
            "status": "invalidated" if run.get("invalidated_at") else run["status"],
            "pass@1": format_pass_at_k(_summary_value(run, "pass_at_1")),
            "pass@3": format_pass_at_k(_summary_value(run, "pass_at_3")),
            "pass^3": format_pass_at_k(_summary_value(run, "pass_hat_3")),
            "median_tokens": _summary_value(run, "median_tokens"),
            "median_latency_ms": _summary_value(run, "median_latency_ms"),
            "n_items": _summary_value(run, "n_items"),
            # Errored items are excluded from the pass rates above, so this
            # column is what stops a mostly-broken run from reading well.
            "n_errors": _summary_value(run, "n_errors"),
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

    _render_reference_pin(client, state, selected_run_id)
    _render_lifecycle(client, state.current_project_id, runs, selected_run_id)
    _render_drilldown(client, state.current_project_id, selected_run_id)


def _render_reference_pin(client: Any, state: DashboardState, run_id: str) -> None:
    """Pin from the list, where you can see what you are pinning."""
    if state.current_project_id is None:
        return
    if st.button("Pin as reference run", key="runs_pin_reference"):
        try:
            client.patch_project_settings(state.current_project_id, baseline_run_id=run_id)
        except BeaconApiError as exc:
            st.error(exc.message)
            return
        st.success(f"Reference run: {run_id[:8]}")


def _render_lifecycle(
    client: Any, project_id: str, runs: list[dict[str, Any]], run_id: str
) -> None:
    """Invalidate a bad experiment, or bring one back. Never delete."""
    selected = next((r for r in runs if str(r.get("run_id")) == run_id), None)
    if selected is None:
        return
    if selected.get("invalidated_at"):
        st.caption(f"Invalidated: {selected.get('invalidation_reason') or '(no reason recorded)'}")
        if st.button("Restore this run", key="runs_restore"):
            try:
                client.restore_run(project_id, run_id)
            except BeaconApiError as exc:
                st.error(exc.message)
                return
            st.rerun()
        return
    with st.expander("Invalidate this run (keeps results, leaves every aggregate)"):
        reason = st.text_input("Why is this run invalid?", key="runs_invalidate_reason")
        if st.button("Invalidate", key="runs_invalidate_btn"):
            if not reason.strip():
                st.error("A reason is required: the explanation is the point of keeping the row.")
                return
            try:
                client.invalidate_run(project_id, run_id, reason=reason)
            except BeaconApiError as exc:
                st.error(exc.message)
                return
            st.rerun()


def _render_drilldown(client: Any, project_id: str, run_id: str) -> None:
    """The product's core question: which items failed, and what did gold say?"""
    st.subheader("Results")
    try:
        outcome = st.selectbox(
            "Outcome",
            options=["", "PASS", "FAIL", "DEFER", "ERROR"],
            format_func=lambda value: value or "all",
            key="results_outcome",
        )
        body = client.list_results(project_id, run_id, **({"outcome": outcome} if outcome else {}))
    except BeaconApiError as exc:
        st.error(f"Failed to load results: {exc.message}")
        return

    counts = body.get("outcome_counts", {})
    if counts:
        st.caption(
            "  ·  ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
            + f"  ·  difficulty: {body.get('difficulty_counts', {})}"
        )
    results = body.get("results", [])
    if not results:
        st.caption("No results match.")
        return
    st.dataframe(
        [
            {
                "item": str(row["item_id"])[:8],
                "question": row.get("question", ""),
                "difficulty": row.get("difficulty") or "",
                "outcome": row.get("outcome"),
                "rows (ours/gold)": f"{row.get('candidate_row_count')}/{row.get('gold_row_count')}",
                "mismatch": row.get("mismatch_kind") or "",
            }
            for row in results
        ],
        width="stretch",
        hide_index=True,
    )

    item_ids = [str(row["item_id"]) for row in results]
    item_id = st.selectbox(
        "Compare an item against gold",
        options=item_ids,
        format_func=lambda value: value[:8],
        key="results_item_select",
    )
    try:
        detail = client.get_result(project_id, run_id, item_id)
    except BeaconApiError as exc:
        st.error(exc.message)
        return

    st.markdown(f"**{detail.get('question', '')}**")
    left, right = st.columns(2)
    output = detail.get("output", {})
    verdicts = detail.get("verdicts", [])
    evidence = verdicts[0].get("evidence") if verdicts else None
    with left:
        st.caption("Ours")
        if detail.get("deferred"):
            st.info("Declined to answer.")
        else:
            st.code(output.get("sql") or output.get("answer") or "", language="sql")
        if isinstance(evidence, dict) and evidence.get("candidate_sample") is not None:
            st.dataframe(evidence["candidate_sample"], hide_index=True)
    with right:
        st.caption("Gold")
        gold = detail.get("gold", {})
        st.code(gold.get("sql") or str(gold.get("answer", "")), language="sql")
        if isinstance(evidence, dict) and evidence.get("gold_sample") is not None:
            st.dataframe(evidence["gold_sample"], hide_index=True)
    for verdict in verdicts:
        st.caption(f"{verdict.get('grader')}: {verdict.get('justification')}")


if __name__ == "__main__":  # pragma: no cover
    render()
