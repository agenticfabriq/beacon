"""Global shared-suite leaderboards view."""

from __future__ import annotations

from typing import Any

import streamlit as st

from beacon_ui.dashboard.auth import client_from_state
from beacon_ui.dashboard.client import BeaconApiError


def _score(row: dict[str, Any], *, metric: str) -> Any:
    if metric == "cost":
        return row.get("cost_adjusted_score")
    return row.get("latency_adjusted_score")


def _format_number(value: Any) -> str:
    if isinstance(value, int | float):
        return f"{value:.4g}"
    return "n/a"


def _row(row: dict[str, Any], *, metric: str) -> dict[str, Any]:
    return {
        "team": row.get("team_name"),
        "solution": row.get("solution_name"),
        "version": row.get("solution_version"),
        "pass_at_3": _format_number(row.get("pass_at_3")),
        "median_tokens": _format_number(row.get("median_tokens")),
        "median_latency_ms": _format_number(row.get("median_latency_ms")),
        "score": _format_number(_score(row, metric=metric)),
        "n_items": row.get("n_items"),
        # Errored items are excluded from pass_at_3, so surface the count.
        "n_errors": row.get("n_errors"),
    }


def render() -> None:
    """Render the global shared-suite leaderboards view."""
    suite = st.text_input("Suite", value="bird_minidev_v2")
    metric = st.radio("Metric", options=["cost", "latency"], horizontal=True)

    client = client_from_state()
    try:
        body = (
            client.cost_leaderboard(suite=suite.strip())
            if metric == "cost"
            else client.latency_leaderboard(suite=suite.strip())
        )
    except BeaconApiError as exc:
        st.error(f"Failed to load leaderboard: {exc.message}")
        return

    rows = body.get("rows", []) if isinstance(body, dict) else []
    leaderboard_rows = (
        [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    )

    st.subheader("Global leaderboard")
    if not leaderboard_rows:
        st.caption("No leaderboard rows for this suite.")
        return

    st.dataframe(
        [_row(row, metric=metric) for row in leaderboard_rows],
        width="stretch",
        hide_index=True,
    )


if __name__ == "__main__":  # pragma: no cover
    render()
