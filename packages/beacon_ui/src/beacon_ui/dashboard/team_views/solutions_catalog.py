"""Team solutions catalog view."""

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


def _list_body(body: Any, *, key: str) -> list[dict[str, Any]]:
    value = body.get(key, []) if isinstance(body, dict) else body
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _load_catalog(team_id: str) -> list[dict[str, Any]]:
    client = client_from_state()
    try:
        return _list_body(client._get(f"/v1/teams/{team_id}/solutions"), key="solutions")  # noqa: SLF001
    except BeaconApiError as exc:
        if exc.status == 404:
            return []
        raise


def _solution_row(solution: dict[str, Any]) -> dict[str, Any]:
    solution_id = solution.get("id") or solution.get("solution_id")
    name = solution.get("name") or solution.get("solution_name") or solution_id
    layers = solution.get("layers", [])
    modes = solution.get("supported_modes") or solution.get("modes") or []
    return {
        "solution_id": solution_id,
        "name": name,
        "version": solution.get("version") or solution.get("solution_version") or "n/a",
        "layers": len(layers) if isinstance(layers, list) else 0,
        "modes": ", ".join(str(mode) for mode in modes) if isinstance(modes, list) else str(modes),
    }


def render() -> None:
    """Render the team Solutions Catalog view listing registered SUTs."""
    state = _state()
    if state.current_team_id is None:
        st.info("Select a team.")
        return

    try:
        solutions = _load_catalog(state.current_team_id)
    except BeaconApiError as exc:
        st.error(f"Failed to load team solutions: {exc.message}")
        return

    st.subheader("Team solutions catalog")
    if not solutions:
        st.caption("No team-catalog solutions are available.")
        return

    st.dataframe(
        [_solution_row(solution) for solution in solutions],
        width="stretch",
        hide_index=True,
    )


if __name__ == "__main__":  # pragma: no cover
    render()
