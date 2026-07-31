import pytest
from dashboard_panel_test import (  # type: ignore[import-not-found]
    DashboardWorld,
    run_dashboard_panel,
)
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_suites_panel_renders(
    api_client: TestClient,
    world: DashboardWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = run_dashboard_panel("suites", api_client, world, monkeypatch)
    assert not at.exception, at.exception
