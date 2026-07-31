from typing import Protocol

import pytest
from dashboard_panel_test import ROOT, route_dashboard_httpx  # type: ignore[import-not-found]
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.integration


class _World(Protocol):
    alice_key: str


def test_global_leaderboard_renders(
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_base = str(api_client.base_url).rstrip("/")
    route_dashboard_httpx(api_client, api_base, monkeypatch)
    at = AppTest.from_file(
        str(ROOT / "packages/beacon_ui/src/beacon_ui/dashboard/global_views/leaderboards.py"),
        default_timeout=30,
    )
    at.session_state["api_key"] = world.alice_key
    at.session_state["api_base"] = api_base
    at.run()
    assert not at.exception, at.exception
