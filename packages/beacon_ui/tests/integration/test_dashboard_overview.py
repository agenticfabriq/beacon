import pytest
from dashboard_panel_test import (  # type: ignore[import-not-found]
    DashboardWorld,
    run_dashboard_panel,
)
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_overview_renders_without_error(
    api_client: TestClient,
    world: DashboardWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = run_dashboard_panel("overview", api_client, world, monkeypatch)
    assert not at.exception, at.exception


def test_overview_shows_kpi_tiles(
    api_client: TestClient,
    world: DashboardWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = run_dashboard_panel("overview", api_client, world, monkeypatch)
    assert any(getattr(el, "label", None) for el in at.get("metric"))
