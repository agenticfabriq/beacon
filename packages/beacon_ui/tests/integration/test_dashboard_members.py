from typing import Protocol
from uuid import UUID

import pytest
from beacon_ui.dashboard.panels.members import visible_memberships
from dashboard_panel_test import run_dashboard_panel  # type: ignore[import-not-found]
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _World(Protocol):
    alice_key: str
    acme_team_id: UUID
    chat_to_data_id: UUID


def test_visible_memberships_filters_project_and_team_scope() -> None:
    memberships = visible_memberships(
        {
            "memberships": [
                {
                    "scope_kind": "team",
                    "scope_id": "team-1",
                    "role": "team_admin",
                },
                {
                    "scope_kind": "project",
                    "scope_id": "project-1",
                    "role": "project_owner",
                },
                {
                    "scope_kind": "project",
                    "scope_id": "project-2",
                    "role": "project_viewer",
                },
            ]
        },
        team_id="team-1",
        project_id="project-1",
    )

    assert [row["role"] for row in memberships] == ["team_admin", "project_owner"]


def test_members_panel_renders(
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = run_dashboard_panel("members", api_client, world, monkeypatch)
    assert not at.exception, at.exception
