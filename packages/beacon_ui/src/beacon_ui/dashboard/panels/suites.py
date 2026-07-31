"""Suites panel: eval-item subsets and suite creation."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import streamlit as st

from beacon_ui.dashboard.auth import client_from_state
from beacon_ui.dashboard.client import BeaconApiError
from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def render() -> None:
    """Render the Suites tab listing project suites and a creation form."""
    state = _state()
    if state.current_project_id is None:
        st.info("Select a project.")
        return

    client = client_from_state()
    try:
        suites = client.list_project_suites(state.current_project_id)
    except BeaconApiError as exc:
        st.error(f"Failed to load suites: {exc.message}")
        return

    st.subheader("Suites in this project")
    if suites:
        st.dataframe(
            [
                {
                    "suite_id": suite["suite_id"],
                    "name": suite["name"],
                    "kind": suite["kind"],
                    "item_count": suite["item_count"],
                    "created_at": suite["created_at"],
                }
                for suite in suites
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No suites yet.")

    st.divider()
    st.subheader("Create a new suite")
    kind = st.radio("Kind", options=["curated", "manual"], horizontal=True)
    name = st.text_input("Name", placeholder="e.g. regression-50")

    if kind == "curated":
        source = st.text_input(
            "Source suite",
            value="bird_minidev_v2",
            help="Full suite to subset from",
        )
        size = st.number_input("Target size", min_value=10, max_value=1000, value=50)
        if st.button(
            "Create curated suite",
            type="primary",
            disabled=not (name and source),
            key="suites_create_curated",
        ):
            try:
                client.create_suite(
                    state.current_project_id,
                    name=name,
                    kind="curated",
                    source_suite=source,
                    target_size=int(size),
                )
            except BeaconApiError as exc:
                st.error(exc.message)
            else:
                st.success("Suite created.")
                st.rerun()
        return

    ids = st.text_area(
        "Eval item IDs",
        help="Paste one UUID per line from the team-level Eval items view.",
    )
    item_ids = [line.strip() for line in ids.splitlines() if line.strip()]
    if st.button(
        "Create manual suite",
        type="primary",
        disabled=not (name and item_ids),
        key="suites_create_manual",
    ):
        try:
            client.create_suite(
                state.current_project_id,
                name=name,
                kind="manual",
                item_ids=item_ids,
            )
        except BeaconApiError as exc:
            st.error(exc.message)
        else:
            st.success("Suite created.")
            st.rerun()


if __name__ == "__main__":  # pragma: no cover
    render()
