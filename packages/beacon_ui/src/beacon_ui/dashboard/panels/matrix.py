"""Results: one row per system · version · model · config.

The page the hand-maintained benchmark tracker becomes. Accuracy and what it
cost sit side by side, so a cheaper configuration that scores the same reads as
a win; a row opens into its layer effect and its runs.

Two readings of correctness per row: exact match, and got-facts (right data,
tolerant shape), both computed by beacon's grader from the same execution.
"""

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


def _pct(value: float | None) -> str:
    """None is unknown, not zero, and must not render as 0.0%."""
    return f"{value:.1%}" if value is not None else "n/a"


def _row_key(row: dict[str, Any]) -> str:
    label = row.get("config_label") or "(unlabelled)"
    system = f"{row.get('solution_name')} {row.get('solution_version')}"
    return f"{system} · {row.get('model_id')} · {label}"


def render() -> None:
    """Render the results matrix with a difficulty facet and per-row detail."""
    state = _state()
    if state.current_project_id is None or state.current_suite_id is None:
        st.info("Pick a benchmark.")
        return

    client = client_from_state()
    try:
        counts = client.results_matrix(state.current_project_id, suite_id=state.current_suite_id)[
            "difficulty_counts"
        ]
        levels = [key for key in sorted(counts) if key != "unknown"]
        difficulty = st.selectbox(
            "Difficulty",
            options=["", *levels],
            format_func=lambda value: (
                f"{value} ({counts[value]})" if value else f"all ({sum(counts.values())})"
            ),
            key="matrix_difficulty",
        )
        filters: dict[str, Any] = {"suite_id": state.current_suite_id}
        if difficulty:
            filters["difficulty"] = difficulty
        body = client.results_matrix(state.current_project_id, **filters)
    except BeaconApiError as exc:
        st.error(f"Failed to load the matrix: {exc.message}")
        return

    rows = body.get("rows", [])
    if not rows:
        st.caption("No graded runs in this benchmark yet.")
        return

    st.dataframe(
        [
            {
                "system": f"{row.get('solution_name')} {row.get('solution_version')}",
                "model": row.get("model_id") or "",
                "config": row.get("config_label") or "",
                "runs": row.get("n_runs"),
                "graded": row.get("n_graded"),
                "EX": _pct(row.get("ex_rate")),
                "got-facts": _pct(row.get("got_facts_rate")),
                "deferred": _pct(row.get("defer_rate")),
                "wrong": _pct(row.get("wrong_rate")),
                "infra": row.get("n_errors"),
                "median tokens": row.get("median_tokens"),
                "median ms": row.get("median_runtime_ms"),
            }
            for row in rows
        ],
        width="stretch",
        hide_index=True,
    )

    selected = st.selectbox(
        "Open a row",
        options=list(range(len(rows))),
        format_func=lambda index: _row_key(rows[index]),
        key="matrix_row_select",
    )
    row = rows[selected]

    st.subheader("Layer effect")
    st.caption(
        "Each declared layer turned off in turn. A layer that helps this model "
        "may do nothing on another, so this is never one global number."
    )
    try:
        snapshot = client.get_attribution(
            state.current_project_id,
            sut=str(row["solution_id"]),
            suite=state.current_suite_id,
        )
    except BeaconApiError as exc:
        st.caption(f"No attribution snapshot: {exc.message}")
    else:
        layers = snapshot.get("layers", [])
        if not snapshot.get("supported"):
            st.caption("This system declares no ablatable layers.")
        elif not layers:
            st.caption("No sweep has produced a snapshot for this system and benchmark yet.")
        else:
            st.dataframe(layers, width="stretch", hide_index=True)

    st.subheader("Runs in this row")
    try:
        runs = client.list_runs(
            state.current_project_id,
            suite_id=state.current_suite_id,
            model_id=row.get("model_id"),
            config_digest=row.get("config_digest"),
            limit=50,
        )
    except BeaconApiError as exc:
        st.error(exc.message)
        return
    st.dataframe(
        [
            {
                "run_id": str(run["run_id"])[:8],
                "arm": run.get("sweep_arm") or "",
                "status": run["status"],
                "pass@1": run.get("summary", {}).get("pass_at_1"),
                "n_items": run.get("summary", {}).get("n_items"),
                "started_at": run.get("started_at"),
            }
            for run in runs
        ],
        width="stretch",
        hide_index=True,
    )


if __name__ == "__main__":  # pragma: no cover
    render()
