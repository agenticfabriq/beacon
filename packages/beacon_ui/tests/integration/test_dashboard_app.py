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


def test_dashboard_app_renders_the_v3_layout(
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _app(api_client=api_client, world=world, monkeypatch=monkeypatch).run()
    assert not at.exception, at.exception


def test_the_app_resolves_a_benchmark_automatically(
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A result must be one click away: team, project and benchmark are
    resolved on load rather than demanded as navigation steps."""
    at = _app(api_client=api_client, world=world, monkeypatch=monkeypatch).run()

    assert not at.exception
    assert at.session_state["current_suite_id"]


def test_the_navigation_is_the_v3_set(
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _app(api_client=api_client, world=world, monkeypatch=monkeypatch).run()

    nav = next(radio for radio in at.radio if radio.key == "nav_page")
    assert list(nav.options) == ["Results", "Runs", "Questions", "Benchmarks", "Settings"]
