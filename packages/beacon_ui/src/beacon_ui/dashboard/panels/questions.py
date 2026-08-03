"""Questions: the gold this benchmark scores against.

Beacon does not author gold. Public corpora are ingested; customer gold is
curated and reviewed in the semantic layer and arrives as an approved export.
This page lists and filters — it does not edit.
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


def render() -> None:
    """Render the questions listing with difficulty and source facets."""
    state = _state()
    if state.current_project_id is None or state.current_suite_id is None:
        st.info("Pick a benchmark.")
        return

    client = client_from_state()
    try:
        facets = client.list_suite_items(state.current_project_id, state.current_suite_id, limit=1)
        columns = st.columns(2)
        with columns[0]:
            difficulty = st.selectbox(
                "Difficulty",
                options=["", *sorted(facets.get("difficulty_counts", {}))],
                format_func=lambda value: value or "all",
                key="questions_difficulty",
            )
        with columns[1]:
            source = st.selectbox(
                "Source",
                options=["", *sorted(facets.get("source_counts", {}))],
                format_func=lambda value: value or "all",
                key="questions_source",
            )
        filters: dict[str, Any] = {"limit": 200}
        if difficulty:
            filters["difficulty"] = difficulty
        if source:
            filters["source"] = source
        body = client.list_suite_items(state.current_project_id, state.current_suite_id, **filters)
    except BeaconApiError as exc:
        st.error(f"Failed to load questions: {exc.message}")
        return

    st.caption(
        f"{body.get('total', 0)} questions"
        f"  ·  difficulty {body.get('difficulty_counts', {})}"
        f"  ·  source {body.get('source_counts', {})}"
    )
    items = body.get("items", [])
    if not items:
        st.caption("No questions match.")
        return
    st.dataframe(
        [
            {
                "item": str(row["item_id"])[:8],
                "question": row.get("question", ""),
                "difficulty": row.get("difficulty") or "",
                "database": row.get("database") or "",
                "source": row.get("source") or "",
                # Imported gold carries the tolerance its reviewer approved;
                # blank means beacon's default applies.
                "tolerance": str(row["tolerance"]) if row.get("tolerance") else "default",
                "gold": "sql" if row.get("has_gold_sql") else "answer",
            }
            for row in items
        ],
        width="stretch",
        hide_index=True,
    )


if __name__ == "__main__":  # pragma: no cover
    render()
