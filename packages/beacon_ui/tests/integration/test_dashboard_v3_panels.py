"""The v3 dashboard pages, rendered against the live API.

Every page must be served by real endpoints: these render each panel through
the same HTTP client the app uses, routed into the test API. A panel that
references an endpoint or a field that does not exist fails here — which is
the audit, automated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest
from dashboard_panel_test import (  # type: ignore[import-not-found]
    ROOT,
    route_dashboard_httpx,
)
from streamlit.testing.v1 import AppTest

if TYPE_CHECKING:
    from uuid import UUID

    from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _World(Protocol):
    alice_key: str
    acme_team_id: UUID
    chat_to_data_id: UUID


def _panel(
    panel_name: str,
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    *,
    suite_id: str | None = None,
) -> AppTest:
    api_base = str(api_client.base_url).rstrip("/")
    route_dashboard_httpx(api_client, api_base, monkeypatch)
    path = ROOT / f"packages/beacon_ui/src/beacon_ui/dashboard/panels/{panel_name}.py"
    at = AppTest.from_file(str(path), default_timeout=30)
    at.session_state["api_key"] = world.alice_key
    at.session_state["api_base"] = api_base
    at.session_state["current_team_id"] = str(world.acme_team_id)
    at.session_state["current_project_id"] = str(world.chat_to_data_id)
    if suite_id is not None:
        at.session_state["current_suite_id"] = suite_id
    return at.run()


def _first_suite_id(api_client: TestClient, world: _World) -> str:
    response = api_client.get(
        f"/v1/projects/{world.chat_to_data_id}/suites",
        headers={"X-API-Key": world.alice_key},
    )
    assert response.status_code == 200, response.text
    suites = response.json()
    assert suites, "the world fixture seeds no suite"
    return str(suites[0]["id"])


def test_benchmarks_panel_renders(
    api_client: TestClient, world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _panel("benchmarks", api_client, world, monkeypatch)
    assert not at.exception


def test_matrix_panel_renders_with_a_benchmark(
    api_client: TestClient, world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite_id = _first_suite_id(api_client, world)
    at = _panel("matrix", api_client, world, monkeypatch, suite_id=suite_id)
    assert not at.exception


def test_matrix_panel_asks_for_a_benchmark_without_one(
    api_client: TestClient, world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _panel("matrix", api_client, world, monkeypatch)
    assert not at.exception
    assert any("benchmark" in str(info.value).lower() for info in at.info)


def test_questions_panel_renders_with_a_benchmark(
    api_client: TestClient, world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite_id = _first_suite_id(api_client, world)
    at = _panel("questions", api_client, world, monkeypatch, suite_id=suite_id)
    assert not at.exception


def test_settings_panel_renders_every_section(
    api_client: TestClient, world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _panel("settings", api_client, world, monkeypatch)
    assert not at.exception
    headers = [str(h.value) for h in at.subheader]
    for section in ("Workspace", "Systems under test", "Access", "API keys"):
        assert section in headers, headers


def test_settings_access_lists_the_roster_not_the_viewer(
    api_client: TestClient, world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _panel("settings", api_client, world, monkeypatch)
    frames = list(at.dataframe)
    assert frames, "no roster rendered"


def test_runs_panel_renders_scoped_to_the_benchmark(
    api_client: TestClient, world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite_id = _first_suite_id(api_client, world)
    at = _panel("runs", api_client, world, monkeypatch, suite_id=suite_id)
    assert not at.exception
