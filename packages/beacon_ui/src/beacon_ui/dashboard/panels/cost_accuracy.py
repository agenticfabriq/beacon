"""Cost/accuracy panel: frontier scatter for pass@3 versus cost or latency."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

import plotly.graph_objects as go  # type: ignore[import-untyped]
import streamlit as st

from beacon_ui.dashboard.auth import client_from_state
from beacon_ui.dashboard.client import BeaconApiError
from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping, Sequence

Metric = Literal["median_tokens", "median_latency_ms"]


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _summary_number(run: dict[str, Any], key: str) -> float | None:
    summary = run.get("summary")
    if not isinstance(summary, dict):
        return None
    return _number(summary.get(key))


def _metric_label(metric: Metric) -> str:
    return "median tokens" if metric == "median_tokens" else "median latency ms"


def build_frontier_points(
    runs: Sequence[dict[str, Any]],
    *,
    metric: Metric,
) -> list[dict[str, Any]]:
    """Project runs onto frontier points of (cost-or-latency, pass@3)."""
    points: list[dict[str, Any]] = []
    for run in runs:
        x_value = _summary_number(run, metric)
        y_value = _summary_number(run, "pass_at_3")
        if x_value is None or y_value is None:
            continue

        run_id = str(run.get("run_id", "run"))
        mode = str(run.get("mode", "run"))
        points.append(
            {
                "label": f"{run_id[:8]} - {mode}",
                "x": x_value,
                "y": y_value,
                "mode": mode,
            }
        )
    return points


def build_frontier_figure(points: Sequence[dict[str, Any]], *, metric: Metric) -> go.Figure:
    """Build the Plotly scatter of pass@3 versus cost or latency, grouped by mode."""
    fig = go.Figure()
    points_by_mode: dict[str, list[dict[str, Any]]] = {}
    for point in points:
        points_by_mode.setdefault(str(point["mode"]), []).append(point)

    for mode_name, mode_points in points_by_mode.items():
        fig.add_trace(
            go.Scatter(
                x=[point["x"] for point in mode_points],
                y=[point["y"] for point in mode_points],
                mode="markers+text",
                name=mode_name,
                text=[point["label"] for point in mode_points],
                textposition="top center",
                marker={"size": 10},
            )
        )

    fig.update_layout(
        title=f"pass@3 vs {_metric_label(metric)}",
        xaxis_title=_metric_label(metric),
        yaxis_title="pass@3",
        height=500,
    )
    return fig


def render() -> None:
    """Render the Cost/Accuracy tab with a pass@3 vs cost-or-latency scatter."""
    state = _state()
    if state.current_project_id is None:
        st.info("Select a project.")
        return

    metric = st.radio(
        "X-axis",
        options=["median_tokens", "median_latency_ms"],
        horizontal=True,
        format_func=_metric_label,
    )

    client = client_from_state()
    try:
        runs = client.list_runs(state.current_project_id, limit=200, status="success")
    except BeaconApiError as exc:
        st.error(exc.message)
        return

    points = build_frontier_points(runs, metric=cast("Metric", metric))
    if not points:
        st.info("No completed runs with both metrics yet.")
        return

    st.plotly_chart(
        build_frontier_figure(points, metric=cast("Metric", metric)),
        width="stretch",
    )
    st.caption("Up-left is Pareto-optimal: higher pass@3 at lower cost or latency.")


if __name__ == "__main__":  # pragma: no cover
    render()
