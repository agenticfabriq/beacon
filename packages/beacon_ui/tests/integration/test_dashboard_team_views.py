from typing import Protocol
from uuid import UUID

import pytest
from dashboard_panel_test import ROOT, route_dashboard_httpx  # type: ignore[import-not-found]
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.integration


class _World(Protocol):
    alice_key: str
    acme_team_id: UUID


@pytest.mark.parametrize(
    "view_module",
    ["solutions_catalog", "members", "secrets"],
)
def test_team_view_renders(
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    view_module: str,
) -> None:
    api_base = str(api_client.base_url).rstrip("/")
    route_dashboard_httpx(api_client, api_base, monkeypatch)
    at = AppTest.from_file(
        str(ROOT / f"packages/beacon_ui/src/beacon_ui/dashboard/team_views/{view_module}.py"),
        default_timeout=30,
    )
    at.session_state["api_key"] = world.alice_key
    at.session_state["api_base"] = api_base
    at.session_state["current_team_id"] = str(world.acme_team_id)
    at.run()
    assert not at.exception, f"{view_module}: {at.exception}"
