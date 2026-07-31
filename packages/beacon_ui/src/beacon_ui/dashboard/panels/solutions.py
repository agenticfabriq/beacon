"""Solutions panel: SUTs configured for this project."""

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


def _catalog_rows(body: Any) -> list[dict[str, Any]]:
    value = body.get("solutions", []) if isinstance(body, dict) else body
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _load_team_catalog(team_id: str | None) -> list[dict[str, Any]]:
    if team_id is None:
        return []
    client = client_from_state()
    try:
        return _catalog_rows(client._get(f"/v1/teams/{team_id}/solutions"))  # noqa: SLF001
    except BeaconApiError as exc:
        if exc.status == 404:
            return []
        raise


def render() -> None:
    """Render the Solutions tab to attach or detach SUTs for a project."""
    state = _state()
    if state.current_project_id is None:
        st.info("Select a project.")
        return

    client = client_from_state()
    try:
        attached = client.list_project_solutions(state.current_project_id)
        catalog = _load_team_catalog(state.current_team_id)
    except BeaconApiError as exc:
        st.error(f"Failed to load solutions: {exc.message}")
        return

    st.subheader("Solutions attached to this project")
    if not attached:
        st.caption("No solutions attached.")
    else:
        st.dataframe(
            [
                {
                    "solution_id": solution["solution_id"],
                    "name": solution["solution_name"],
                    "version": solution["solution_version"],
                    "added_at": solution["added_at"],
                }
                for solution in attached
            ],
            width="stretch",
            hide_index=True,
        )
        remove_target = st.selectbox(
            "Remove a solution",
            options=[str(solution["solution_id"]) for solution in attached],
            format_func=lambda solution_id: next(
                str(solution["solution_name"])
                for solution in attached
                if str(solution["solution_id"]) == solution_id
            ),
            key="solutions_remove",
        )
        if st.button("Detach", type="secondary", key="solutions_detach_btn"):
            try:
                client.remove_project_solution(state.current_project_id, remove_target)
            except BeaconApiError as exc:
                st.error(exc.message)
            else:
                st.success("Solution detached.")
                st.rerun()

    st.subheader("Add from team catalog")
    attached_ids = {str(solution["solution_id"]) for solution in attached}
    available = [row for row in catalog if str(row.get("id")) not in attached_ids]
    if not available:
        st.caption("No team-catalog solutions available to attach.")
        return

    add_target = st.selectbox(
        "Solution",
        options=[str(row["id"]) for row in available if "id" in row],
        format_func=lambda solution_id: next(
            f"{row.get('name', row.get('id'))} v{row.get('version', 'n/a')}"
            for row in available
            if str(row.get("id")) == solution_id
        ),
        key="solutions_add_select",
    )
    if st.button("Attach", type="primary", key="solutions_add_btn"):
        try:
            client.add_project_solution(state.current_project_id, solution_id=add_target)
        except BeaconApiError as exc:
            st.error(exc.message)
        else:
            st.success("Solution attached.")
            st.rerun()


if __name__ == "__main__":  # pragma: no cover
    render()
