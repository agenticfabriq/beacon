from typing import Protocol
from uuid import UUID

import pytest
from beacon_ui.dashboard.panels.settings import selected_project
from dashboard_panel_test import run_dashboard_panel  # type: ignore[import-not-found]
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _World(Protocol):
    alice_key: str
    acme_team_id: UUID
    chat_to_data_id: UUID


def test_selected_project_matches_by_id() -> None:
    project = selected_project(
        [
            {"id": "p1", "name": "one"},
            {"id": "p2", "name": "two", "gate_policy": {"mode": "warn"}},
        ],
        "p2",
    )

    assert project == {"id": "p2", "name": "two", "gate_policy": {"mode": "warn"}}


def test_settings_panel_renders(
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = run_dashboard_panel("settings", api_client, world, monkeypatch)
    assert not at.exception, at.exception
