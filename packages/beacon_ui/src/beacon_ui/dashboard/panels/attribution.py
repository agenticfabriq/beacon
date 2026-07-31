"""Attribution panel: per-layer pass@3 deltas with CI and adjusted p-values."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import plotly.graph_objects as go  # type: ignore[import-untyped]
import streamlit as st

from beacon_ui.dashboard.auth import client_from_state
from beacon_ui.dashboard.client import BeaconApiError
from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping, Sequence


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def _number(row: dict[str, Any], key: str) -> float:
    value = row.get(key)
    return float(value) if isinstance(value, int | float) else 0.0


def _sorted_layers(layers: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(layers, key=lambda row: -abs(_number(row, "delta_pass_at_3")))


def build_attribution_figure(layers: Sequence[dict[str, Any]]) -> go.Figure:
    """Build the Plotly figure of per-layer delta pass@3 with CI bars."""
    sorted_layers = _sorted_layers(layers)
    names = [str(row.get("layer", "layer")) for row in sorted_layers]
    deltas = [_number(row, "delta_pass_at_3") for row in sorted_layers]
    ci_lows = [_number(row, "ci_low") for row in sorted_layers]
    ci_highs = [_number(row, "ci_high") for row in sorted_layers]
    bh_ps = [_number(row, "bh_p") for row in sorted_layers]

    err_upper = [high - delta for high, delta in zip(ci_highs, deltas, strict=True)]
    err_lower = [delta - low for delta, low in zip(deltas, ci_lows, strict=True)]
    labels = [f"p={p_value:.3f}{' *' if p_value < 0.05 else ''}" for p_value in bh_ps]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=names,
            y=deltas,
            error_y={
                "type": "data",
                "symmetric": False,
                "array": err_upper,
                "arrayminus": err_lower,
                "thickness": 1.5,
                "width": 6,
            },
            text=labels,
            textposition="outside",
            marker_color=["#2ca02c" if delta >= 0 else "#d62728" for delta in deltas],
        )
    )
    fig.update_layout(
        title="Per-layer delta pass@3 (95% bootstrap CI; BH-adjusted p)",
        xaxis_title="Layer",
        yaxis_title="delta pass@3",
        height=400,
        showlegend=False,
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
    return fig


def _format_pct(value: Any) -> str:
    return f"{float(value):+.1%}" if isinstance(value, int | float) else "n/a"


def render() -> None:
    """Render the Attribution tab with per-layer delta pass@3 and CI."""
    state = _state()
    if state.current_project_id is None:
        st.info("Select a project.")
        return

    client = client_from_state()
    try:
        solutions = client.list_project_solutions(state.current_project_id)
        suites = client.list_project_suites(state.current_project_id)
    except BeaconApiError as exc:
        st.error(exc.message)
        return

    if not solutions or not suites:
        st.info("Attach a solution and create a suite first.")
        return

    cols = st.columns(2)
    with cols[0]:
        solution_id = st.selectbox(
            "SUT",
            options=[str(solution["solution_id"]) for solution in solutions],
            format_func=lambda selected: next(
                str(solution["solution_name"])
                for solution in solutions
                if str(solution["solution_id"]) == selected
            ),
        )
    with cols[1]:
        suite_id = st.selectbox(
            "Suite",
            options=[str(suite["suite_id"]) for suite in suites],
            format_func=lambda selected: next(
                str(suite["name"]) for suite in suites if str(suite["suite_id"]) == selected
            ),
        )

    try:
        snapshot = client.get_attribution(
            state.current_project_id,
            sut=solution_id,
            suite=suite_id,
        )
    except BeaconApiError as exc:
        st.error(exc.message)
        return

    if not snapshot.get("supported", False):
        st.info("This SUT does not declare layers. Substrate attribution is unavailable.")
        return

    raw_layers = snapshot.get("layers", [])
    layers = (
        [row for row in raw_layers if isinstance(row, dict)] if isinstance(raw_layers, list) else []
    )
    if not layers:
        st.warning("No attribution data yet. Run NIGHTLY_LOO to populate.")
        return

    layers = _sorted_layers(layers)
    st.plotly_chart(build_attribution_figure(layers), width="stretch")
    st.caption(
        f"Computed at: {snapshot.get('computed_at') or 'n/a'}. "
        "Asterisk on p-value indicates significance after Benjamini-Hochberg correction "
        "at alpha 0.05."
    )

    st.subheader("Per-layer details")
    st.dataframe(
        [
            {
                "layer": row.get("layer"),
                "delta pass@3": f"{_number(row, 'delta_pass_at_3'):+.3f}",
                "95% CI": f"[{_number(row, 'ci_low'):+.3f}, {_number(row, 'ci_high'):+.3f}]",
                "McNemar p": f"{_number(row, 'mcnemar_p'):.4f}",
                "BH-adj p": f"{_number(row, 'bh_p'):.4f}",
                "token delta %": _format_pct(row.get("median_token_delta_pct")),
            }
            for row in layers
        ],
        width="stretch",
        hide_index=True,
    )


if __name__ == "__main__":  # pragma: no cover
    render()
