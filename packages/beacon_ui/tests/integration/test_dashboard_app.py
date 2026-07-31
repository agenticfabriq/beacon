from importlib import import_module
from typing import Any, Protocol, cast
from uuid import UUID

import pytest
from dashboard_panel_test import ROOT, route_dashboard_httpx  # type: ignore[import-not-found]
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.integration

dashboard_app = cast("Any", import_module("beacon_ui.dashboard.app"))


class _World(Protocol):
    alice_key: str
    acme_team_id: UUID
    chat_to_data_id: UUID


def _app(
    *,
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
) -> AppTest:
    api_base = str(api_client.base_url).rstrip("/")
    route_dashboard_httpx(api_client, api_base, monkeypatch)
    at = AppTest.from_file(
        str(ROOT / "packages/beacon_ui/src/beacon_ui/dashboard/app.py"),
        default_timeout=30,
    )
    at.session_state["api_key"] = world.alice_key
    at.session_state["api_base"] = api_base
    at.session_state["current_team_id"] = str(world.acme_team_id)
    at.session_state["current_project_id"] = str(world.chat_to_data_id)
    return at


def test_dashboard_app_renders_project_workspace(
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _app(api_client=api_client, world=world, monkeypatch=monkeypatch)
    at.session_state["nav_section"] = "project"
    at.run()
    assert not at.exception, at.exception


def test_project_workspace_uses_compact_cost_accuracy_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    labels: list[str] = []

    class _Tab:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_tabs(tab_labels: list[str]) -> list[_Tab]:
        labels.extend(tab_labels)
        return [_Tab() for _ in tab_labels]

    monkeypatch.setattr(dashboard_app.st, "tabs", fake_tabs)
    monkeypatch.setattr(dashboard_app.p_overview, "render", lambda: None)
    monkeypatch.setattr(dashboard_app.p_runs, "render", lambda: None)
    monkeypatch.setattr(dashboard_app.p_solutions, "render", lambda: None)
    monkeypatch.setattr(dashboard_app.p_suites, "render", lambda: None)
    monkeypatch.setattr(dashboard_app.p_attribution, "render", lambda: None)
    monkeypatch.setattr(dashboard_app.p_cost_accuracy, "render", lambda: None)
    monkeypatch.setattr(dashboard_app.p_review_queue, "render", lambda: None)
    monkeypatch.setattr(dashboard_app.p_members, "render", lambda: None)
    monkeypatch.setattr(dashboard_app.p_settings, "render", lambda: None)

    dashboard_app._render_project_workspace()

    assert "Cost/Accuracy" in labels
    assert "Cost / Accuracy" not in labels


@pytest.mark.parametrize(
    "team_subview",
    ["solutions_catalog", "eval_items", "members", "secrets"],
)
def test_dashboard_app_renders_team_views(
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    team_subview: str,
) -> None:
    at = _app(api_client=api_client, world=world, monkeypatch=monkeypatch)
    at.session_state["nav_section"] = "team"
    at.session_state["nav_team_view"] = team_subview
    at.run()
    assert not at.exception, f"{team_subview}: {at.exception}"


def test_dashboard_app_renders_global_leaderboard(
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _app(api_client=api_client, world=world, monkeypatch=monkeypatch)
    at.session_state["nav_section"] = "global"
    at.run()
    assert not at.exception, at.exception
