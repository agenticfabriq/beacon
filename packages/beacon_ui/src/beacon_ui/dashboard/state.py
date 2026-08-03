"""Dashboard session-state facade.

Credentials come from the session first, and fall back to the same ambient
context the CLI uses: ``BEACON_API_KEY`` / ``BEACON_API_BASE`` and the
``~/.beacon/ctx.json`` written by ``beacon login``. Without that fallback the
dashboard could not be pre-authenticated for a demo, a kiosk or an automated
screenshot, and every session began by hand-pasting a key -- while the rest of
the product had read those settings all along.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_ui.cli.ctx import load_context

if TYPE_CHECKING:
    from collections.abc import MutableMapping


def _ambient() -> tuple[str | None, str | None]:
    """Return the API key and base the environment supplies, if any."""
    try:
        context = load_context()
    except OSError:  # pragma: no cover - an unreadable context is simply absent
        return None, None
    return context.api_key, context.api_base


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
        """Return the session's API key, or the one the environment supplies.

        An explicit sign-in wins; logging out clears it and is not undone by the
        ambient key, so signing out of a pre-authenticated dashboard works.
        """
        value = self.bag.get("api_key")
        if isinstance(value, str) and value:
            return value
        if "api_key" in self.bag:
            return None
        return _ambient()[0]

    @property
    def api_base(self) -> str:
        """Return the session's API base, the environment's, or the local default."""
        value = self.bag.get("api_base")
        if isinstance(value, str) and value:
            return value
        return _ambient()[1] or "http://localhost:8000"

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
        prior = self.current_project_id
        self.bag["current_project_id"] = project_id
        if prior != project_id:
            self.bag["current_suite_id"] = None

    @property
    def current_suite_id(self) -> str | None:
        """The selected benchmark. Picking one is the only step before a result."""
        value = self.bag.get("current_suite_id")
        return value if isinstance(value, str) and value else None

    def select_suite(self, suite_id: str | None) -> None:
        """Switch the active benchmark."""
        self.bag["current_suite_id"] = suite_id
