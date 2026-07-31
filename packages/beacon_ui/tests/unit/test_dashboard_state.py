from __future__ import annotations

from beacon_ui.dashboard.state import DashboardState


def test_state_defaults_to_unauthenticated() -> None:
    state = DashboardState({})

    assert state.is_authenticated() is False
    assert state.current_team_id is None
    assert state.current_project_id is None


def test_set_credentials_sets_authenticated() -> None:
    bag: dict[str, object] = {}
    state = DashboardState(bag)

    state.set_credentials(api_key="bcn_test_xyz", api_base="http://localhost:8000")

    assert state.is_authenticated() is True
    assert bag["api_key"] == "bcn_test_xyz"
    assert state.api_base == "http://localhost:8000"


def test_select_team_clears_project_when_team_changes() -> None:
    bag: dict[str, object] = {"current_team_id": "team-1", "current_project_id": "proj-A"}
    state = DashboardState(bag)

    state.select_team("team-2")

    assert bag["current_team_id"] == "team-2"
    assert bag["current_project_id"] is None


def test_logout_clears_all_state() -> None:
    bag: dict[str, object] = {
        "api_key": "x",
        "api_base": "http://api",
        "current_team_id": "y",
        "current_project_id": "z",
    }

    DashboardState(bag).logout()

    assert bag.get("api_key") is None
    assert bag.get("api_base") is None
    assert bag.get("current_team_id") is None
    assert bag.get("current_project_id") is None
