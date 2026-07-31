"""Dashboard login helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import streamlit as st

from beacon_ui.dashboard.client import BeaconApiClient, BeaconApiError
from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _state() -> DashboardState:
    return DashboardState(cast("MutableMapping[str, object]", st.session_state))


def login_form() -> bool:
    """Render the login form and return True when the session is authenticated."""
    state = _state()
    if state.is_authenticated():
        return True

    st.title("Beacon - sign in")
    st.caption("Enter your personal API key.")
    api_base = st.text_input("API base URL", value=state.api_base)
    api_key = st.text_input("API key", type="password")

    if st.button("Sign in", type="primary"):
        if not api_key:
            st.error("API key required.")
            return False
        try:
            BeaconApiClient(base_url=api_base, api_key=api_key).me()
        except BeaconApiError as exc:
            st.error(f"Authentication failed: {exc.message}")
            return False
        state.set_credentials(api_key=api_key, api_base=api_base)
        st.rerun()
    return False


def client_from_state() -> BeaconApiClient:
    """Build a BeaconApiClient from the authenticated dashboard session."""
    state = _state()
    if not state.is_authenticated() or state.api_key is None:
        raise RuntimeError("Not authenticated")
    return BeaconApiClient(base_url=state.api_base, api_key=state.api_key)
