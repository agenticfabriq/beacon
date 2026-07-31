"""Team secrets references view."""

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


def _load_secrets(team_id: str) -> list[dict[str, Any]]:
    client = client_from_state()
    try:
        return _list_body(client._get(f"/v1/teams/{team_id}/secrets"), key="secrets")  # noqa: SLF001
    except BeaconApiError as exc:
        if exc.status == 404:
            return []
        raise


def _secret_row(secret: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": secret.get("name") or secret.get("key") or secret.get("secret_ref"),
        "scope": secret.get("scope", "team"),
        "updated_at": secret.get("updated_at") or secret.get("created_at"),
        "secret_ref": secret.get("secret_ref") or secret.get("id"),
    }


def render() -> None:
    """Render the team Secrets view listing secret references (no values)."""
    state = _state()
    if state.current_team_id is None:
        st.info("Select a team.")
        return

    try:
        secrets = _load_secrets(state.current_team_id)
    except BeaconApiError as exc:
        st.error(f"Failed to load secret references: {exc.message}")
        return

    st.subheader("Team secret references")
    st.caption("Only secret references are shown.")
    if not secrets:
        st.caption("No team secret references are available.")
        return

    st.dataframe(
        [_secret_row(secret) for secret in secrets],
        width="stretch",
        hide_index=True,
    )


if __name__ == "__main__":  # pragma: no cover
    render()
