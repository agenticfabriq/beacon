"""Dashboard session-state facade."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import MutableMapping


class DashboardState:
    """Small wrapper around Streamlit's session_state mapping."""

    def __init__(self, bag: MutableMapping[str, object]) -> None:
        self.bag = bag

    def is_authenticated(self) -> bool:
        """Return True when the session has a stored API key."""
        return bool(self.api_key)

    def set_credentials(self, *, api_key: str, api_base: str) -> None:
        """Store the user's API key and API base in session state."""
        self.bag["api_key"] = api_key
        self.bag["api_base"] = api_base

    def logout(self) -> None:
        """Clear credentials and the current team/project selection."""
        for key in ("api_key", "api_base", "current_team_id", "current_project_id"):
            self.bag[key] = None

    @property
    def api_key(self) -> str | None:
        """Return the stored API key, or None when unauthenticated."""
        value = self.bag.get("api_key")
        return value if isinstance(value, str) and value else None

    @property
    def api_base(self) -> str:
        """Return the stored API base URL or the local default."""
        value = self.bag.get("api_base")
        return value if isinstance(value, str) and value else "http://localhost:8000"

    @property
    def current_team_id(self) -> str | None:
        """Return the currently selected team ID, if any."""
        value = self.bag.get("current_team_id")
        return value if isinstance(value, str) and value else None

    def select_team(self, team_id: str | None) -> None:
        """Switch the active team and clear the selected project on change."""
        prior = self.current_team_id
        self.bag["current_team_id"] = team_id
        if prior != team_id:
            self.bag["current_project_id"] = None

    @property
    def current_project_id(self) -> str | None:
        """Return the currently selected project ID, if any."""
        value = self.bag.get("current_project_id")
        return value if isinstance(value, str) and value else None

    def select_project(self, project_id: str | None) -> None:
        """Set the active project ID for the session."""
        self.bag["current_project_id"] = project_id
