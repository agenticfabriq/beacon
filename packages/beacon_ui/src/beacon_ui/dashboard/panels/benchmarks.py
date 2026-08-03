"""Benchmarks: the container everything is compared within.

Picking one is the only navigation step before a result. Creating one means
naming a set of questions to score against — what the old "create a suite" was
for, placed next to the thing it creates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import streamlit as st

from beacon_ui.dashboard.auth import client_from_state
from beacon_ui.dashboard.client import BeaconApiError
from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping

_HIERARCHY = """\
**How a result is addressed:** Benchmark → System → Version → Model → Config →
Run → Result. The middle four together are one row in **Results**. Team is the
access boundary, not a level of the hierarchy.
"""


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def render() -> None:
    """Render the benchmark list, counts, and the create form."""
    state = _state()
    if state.current_project_id is None:
        st.info("Select a project in Settings.")
        return

    client = client_from_state()
    st.markdown(_HIERARCHY)
    try:
        suites = client.list_project_suites(state.current_project_id)
        runs = client.list_runs(state.current_project_id, limit=500, include_invalidated=True)
    except BeaconApiError as exc:
        st.error(f"Failed to load benchmarks: {exc.message}")
        return

    runs_by_suite: dict[str, int] = {}
    for run in runs:
        key = str(run.get("suite_id"))
        runs_by_suite[key] = runs_by_suite.get(key, 0) + 1

    if suites:
        st.dataframe(
            [
                {
                    "benchmark": suite.get("name"),
                    "questions": suite.get("item_count"),
                    "runs": runs_by_suite.get(str(suite.get("id")), 0),
                    "created_at": suite.get("created_at"),
                }
                for suite in suites
            ],
            width="stretch",
            hide_index=True,
        )
        options = [str(suite["id"]) for suite in suites]
        chosen = st.selectbox(
            "Open a benchmark",
            options=options,
            index=(
                options.index(state.current_suite_id) if state.current_suite_id in options else 0
            ),
            format_func=lambda suite_id: next(
                str(suite["name"]) for suite in suites if str(suite["id"]) == suite_id
            ),
            key="benchmarks_select",
        )
        if chosen != state.current_suite_id:
            state.select_suite(chosen)
            st.rerun()
    else:
        st.caption("No benchmarks yet.")

    with st.expander("Create a benchmark"):
        name = st.text_input("Name", key="benchmarks_create_name")
        description = st.text_input("Description", key="benchmarks_create_desc")
        if st.button("Create", key="benchmarks_create_btn", disabled=not name.strip()):
            try:
                created = client.create_suite(
                    state.current_project_id,
                    name=name.strip(),
                    description=description,
                    kind="manual",
                )
            except BeaconApiError as exc:
                st.error(exc.message)
                return
            state.select_suite(str(created.get("id")))
            st.rerun()


if __name__ == "__main__":  # pragma: no cover
    render()
