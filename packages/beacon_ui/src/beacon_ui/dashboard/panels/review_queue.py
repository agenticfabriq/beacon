"""Review queue panel for human-in-the-loop item decisions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import streamlit as st

from beacon_ui.dashboard.auth import client_from_state
from beacon_ui.dashboard.client import BeaconApiError
from beacon_ui.dashboard.components import render_candidates_side_by_side
from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def review_item_question(item: dict[str, Any]) -> str:
    """Extract a human-readable question string from a review-queue item."""
    direct = item.get("question")
    if isinstance(direct, str) and direct:
        return direct

    item_input = _dict_value(item.get("item_input"))
    for key in ("question", "q", "prompt"):
        value = item_input.get(key)
        if isinstance(value, str) and value:
            return value
    return json.dumps(item_input, sort_keys=True) if item_input else "n/a"


def review_item_gold(item: dict[str, Any]) -> Any:
    """Return the recorded gold answer for a review-queue item, if any."""
    if "gold" in item:
        return item["gold"]
    return item.get("gold_answer")


def _review_item_candidates(item: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = _dict_list(item.get("candidates"))
    if candidates:
        return candidates
    metadata = _dict_value(item.get("metadata"))
    return _dict_list(metadata.get("candidates"))


def _reviewer_history(item: dict[str, Any]) -> list[dict[str, Any]]:
    history = _dict_list(item.get("reviewer_history"))
    if history:
        return history
    metadata = _dict_value(item.get("metadata"))
    return _dict_list(metadata.get("reviewer_history"))


def _render_history(item: dict[str, Any]) -> None:
    history = _reviewer_history(item)
    if not history:
        return

    with st.expander(f"Reviewer history ({len(history)})", expanded=False):
        for event in history:
            action = event.get("action", "?")
            actor = event.get("actor", "?")
            decided_at = event.get("decided_at", "n/a")
            reason = event.get("reason", "")
            st.markdown(f"- **{action}** by `{actor}` at {decided_at}: _{reason}_")


def _render_decision_form(
    *,
    client: Any,
    project_id: str,
    item_id: str,
) -> None:
    action_key = f"rq_action_{item_id}"
    reason_key = f"rq_reason_{item_id}"
    defer_key = f"rq_defer_{item_id}"
    submit_key = f"rq_submit_{item_id}"

    action_cols = st.columns([2, 4, 2])
    with action_cols[0]:
        action = st.radio(
            "Decision",
            options=["accept", "reject", "defer"],
            key=action_key,
        )
    with action_cols[1]:
        reason = st.text_area(
            "Reason (required)",
            key=reason_key,
            height=80,
            placeholder="Explain accept, reject, or defer rationale.",
        )
    with action_cols[2]:
        defer_days = (
            st.number_input("Defer days", min_value=1, max_value=90, value=7, key=defer_key)
            if action == "defer"
            else None
        )
        submit = st.button("Submit", type="primary", key=submit_key, disabled=not reason.strip())

    if not submit:
        return

    try:
        client.decide_review(
            project_id,
            item_id,
            action=action,
            reason=reason.strip(),
            defer_days=int(defer_days) if defer_days is not None else None,
        )
    except BeaconApiError as exc:
        st.error(f"Failed to record decision: {exc.message}")
        return

    st.success(f"Recorded {action}.")
    st.rerun()


def _render_item(*, client: Any, project_id: str, item: dict[str, Any]) -> None:
    item_id = str(item.get("item_id", ""))
    with st.container(border=True):
        top = st.columns([4, 1])
        with top[0]:
            st.markdown(f"**{item.get('suite', 'suite')}** - `{item_id[:8]}`")
            st.markdown(f"**Question:** {review_item_question(item)}")
        with top[1]:
            st.caption(f"Tier: {item.get('tier', 'n/a')}")

        with st.expander("Gold answer", expanded=False):
            gold = review_item_gold(item)
            if gold is None:
                st.caption("No gold answer recorded.")
            else:
                st.json(gold)

        st.markdown("**Candidate outputs**")
        render_candidates_side_by_side(_review_item_candidates(item))
        _render_history(item)
        _render_decision_form(client=client, project_id=project_id, item_id=item_id)


def render() -> None:
    """Render the Review Queue tab with per-item decision forms."""
    state = _state()
    if state.current_project_id is None:
        st.info("Select a project.")
        return

    cols = st.columns(3)
    with cols[0]:
        suite_filter = st.text_input("Suite filter", value="", placeholder="bird_minidev_v2")
    with cols[1]:
        page_size = st.number_input("Page size", min_value=5, max_value=50, value=20)
    with cols[2]:
        offset = st.number_input("Offset", min_value=0, value=0, step=max(int(page_size), 1))

    params: dict[str, object] = {"limit": int(page_size), "offset": int(offset)}
    if suite_filter.strip():
        params["suite"] = suite_filter.strip()

    client = client_from_state()
    try:
        queue = client.list_review_queue(state.current_project_id, **params)
    except BeaconApiError as exc:
        st.error(f"Failed to load queue: {exc.message}")
        return

    items = _dict_list(queue.get("items"))
    total = queue.get("total", len(items))
    shown_offset = queue.get("offset", offset)
    st.caption(f"{total} items pending; showing {len(items)} at offset {shown_offset}.")

    if not items:
        st.success("Queue is empty. Nothing pending review.")
        return

    for item in items:
        _render_item(client=client, project_id=state.current_project_id, item=item)


if __name__ == "__main__":  # pragma: no cover
    render()
