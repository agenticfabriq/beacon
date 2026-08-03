from typing import Protocol
from uuid import UUID

import pytest
from beacon_ui.dashboard.panels.cost_accuracy import (
    build_frontier_figure,
    build_frontier_points,
)
from dashboard_panel_test import run_dashboard_panel  # type: ignore[import-not-found]
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _World(Protocol):
    alice_key: str
    acme_team_id: UUID
    chat_to_data_id: UUID


def test_frontier_points_filter_runs_without_cost_metrics() -> None:
    points = build_frontier_points(
        [
            {
                "run_id": "12345678-0000-0000-0000-000000000000",
                "mode": "EVAL",
                "summary": {
                    "pass_at_3": 0.75,
                    "median_tokens": 1200,
                    "median_latency_ms": 800,
                },
            },
            {
                "run_id": "87654321-0000-0000-0000-000000000000",
                "mode": "EVAL",
                "summary": {"pass_at_3": None, "median_tokens": 1000},
            },
        ],
        metric="median_tokens",
    )

    assert points == [
        {
            "label": "12345678 - EVAL",
            "x": 1200.0,
            "y": 0.75,
            "mode": "EVAL",
        }
    ]

    fig = build_frontier_figure(points, metric="median_tokens")
    trace = fig.data[0]
    assert list(trace.x) == [1200.0]
    assert list(trace.y) == [0.75]
    assert list(trace.text) == ["12345678 - EVAL"]


def test_cost_accuracy_panel_renders(
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = run_dashboard_panel("cost_accuracy", api_client, world, monkeypatch)
    assert not at.exception, at.exception
