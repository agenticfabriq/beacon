"""Team eval-item catalog view."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import streamlit as st

from beacon_ui.dashboard.auth import client_from_state
from beacon_ui.dashboard.client import BeaconApiError
from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _question(item: dict[str, Any]) -> str:
    item_input = item.get("item_input", {})
    if isinstance(item_input, dict):
        for key in ("question", "q", "prompt"):
            value = item_input.get(key)
            if isinstance(value, str) and value:
                return value
        return json.dumps(item_input, sort_keys=True)
    return "n/a"


def _item_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": item.get("item_id"),
        "suite": item.get("suite"),
        "tier": item.get("tier"),
        "dataset_version": item.get("dataset_version"),
        "question": _question(item),
    }


def _load_items(*, team_id: str, suite: str | None, tier: str | None) -> list[dict[str, Any]]:
    client = client_from_state()
    body = client._get("/v1/registry/items", team_id=team_id, suite=suite, tier=tier)  # noqa: SLF001
    if not isinstance(body, dict):
        return []
    return _dict_list(body.get("items"))


def _load_findings(team_id: str) -> list[dict[str, Any]]:
    client = client_from_state()
    body = client._get(f"/v1/teams/{team_id}/anti-goodhart", limit=50)  # noqa: SLF001
    if not isinstance(body, dict):
        return []
    return _dict_list(body.get("findings"))


def render() -> None:
    """Render the team Eval Items view with tier and suite filters."""
    state = _state()
    if state.current_team_id is None:
        st.info("Select a team.")
        return

    filters = st.columns(2)
    with filters[0]:
        suite = st.text_input("Suite filter", value="", placeholder="bird_minidev_v2")
    with filters[1]:
        tier = st.selectbox(
            "Tier",
            options=["", "candidate", "human_review", "execution_confirmed", "deprecated"],
        )

    try:
        items = _load_items(
            team_id=state.current_team_id,
            suite=suite.strip() or None,
            tier=tier or None,
        )
        findings = _load_findings(state.current_team_id)
    except BeaconApiError as exc:
        st.error(f"Failed to load eval items: {exc.message}")
        return

    st.subheader("Eval items")
    if items:
        st.dataframe(
            [_item_row(item) for item in items],
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No eval items match these filters.")

    st.subheader("Anti-Goodhart findings")
    if not findings:
        st.success("No active anti-Goodhart findings for this team.")
        return

    st.dataframe(
        [
            {
                "severity": finding.get("severity"),
                "kind": finding.get("kind"),
                "eval_item_id": finding.get("eval_item_id"),
                "detail": finding.get("detail"),
                "created_at": finding.get("created_at"),
            }
            for finding in findings
        ],
        width="stretch",
        hide_index=True,
    )


if __name__ == "__main__":  # pragma: no cover
    render()
