import pytest
from dashboard_panel_test import (  # type: ignore[import-not-found]
    DashboardWorld,
    run_dashboard_panel,
)
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_runs_panel_renders(
    api_client: TestClient,
    world: DashboardWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = run_dashboard_panel("runs", api_client, world, monkeypatch)
    assert not at.exception, at.exception


def test_runs_panel_has_filter_widgets(
    api_client: TestClient,
    world: DashboardWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = run_dashboard_panel("runs", api_client, world, monkeypatch)
    assert any(
        "mode" in str(getattr(selectbox, "label", "")).lower() for selectbox in at.get("selectbox")
    )
