from typing import Protocol
from uuid import UUID

import pytest
from beacon_storage.ids import uuid7
from beacon_storage.models.attribution import Attribution
from beacon_storage.models.runs import HarnessMode
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_ui.dashboard.panels.attribution import build_attribution_figure
from dashboard_panel_test import run_dashboard_panel  # type: ignore[import-not-found]
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _World(Protocol):
    alice_key: str
    acme_team_id: UUID
    chat_to_data_id: UUID
    alice_id: UUID
    acme_solution_id: UUID


def _seed_attributions(session: Session, world: _World) -> UUID:
    suite = SuiteRepo(session).get_by_project_and_name(world.chat_to_data_id, "bird_minidev_v2")
    assert suite is not None

    sweep_id = uuid7()
    run_repo = RunRepo(session)
    baseline = run_repo.create(
        team_id=world.acme_team_id,
        project_id=world.chat_to_data_id,
        solution_id=world.acme_solution_id,
        suite=suite.name,
        dataset_version="v2",
        mode=HarnessMode.NIGHTLY_LOO,
        pass_idx=0,
        parent_sweep_id=sweep_id,
        config={"label": "baseline"},
        created_by=world.alice_id,
    )
    ablated = run_repo.create(
        team_id=world.acme_team_id,
        project_id=world.chat_to_data_id,
        solution_id=world.acme_solution_id,
        suite=suite.name,
        dataset_version="v2",
        mode=HarnessMode.NIGHTLY_LOO,
        pass_idx=1,
        parent_sweep_id=sweep_id,
        config={"label": "ablated"},
        created_by=world.alice_id,
    )
    run_repo.mark_completed(baseline.id)
    run_repo.mark_completed(ablated.id)

    session.add(
        Attribution(
            attribution_id=uuid7(),
            sweep_id=sweep_id,
            project_id=world.chat_to_data_id,
            team_id=world.acme_team_id,
            solution_id=world.acme_solution_id,
            solution_version="0.1",
            suite=suite.name,
            dataset_version="v2",
            layer_name="ontology",
            methodology="LOO",
            baseline_run_id=baseline.id,
            ablated_run_id=ablated.id,
            pass_at_k_baseline={"3": 0.7},
            pass_at_k_ablated={"3": 0.9},
            delta_pass_at_k={
                "3": {
                    "delta": 0.2,
                    "ci_low": 0.1,
                    "ci_high": 0.3,
                    "p": 0.005,
                }
            },
            pass_hat_k_baseline={"3": 0.65},
            pass_hat_k_ablated={"3": 0.83},
            delta_pass_hat_k={
                "3": {
                    "delta": 0.18,
                    "ci_low": 0.08,
                    "ci_high": 0.28,
                    "p": 0.005,
                }
            },
            token_delta_pct=-0.1,
            runtime_delta_pct=None,
            mcnemar_p=0.005,
            bh_adjusted_p=0.01,
            ci_low=0.1,
            ci_high=0.3,
        )
    )
    session.commit()
    return suite.id


def test_attribution_figure_uses_asymmetric_error_bars() -> None:
    fig = build_attribution_figure(
        [
            {
                "layer": "L1",
                "delta_pass_at_3": 0.2,
                "ci_low": 0.1,
                "ci_high": 0.35,
                "bh_p": 0.01,
            },
            {
                "layer": "L2",
                "delta_pass_at_3": -0.1,
                "ci_low": -0.25,
                "ci_high": -0.02,
                "bh_p": 0.2,
            },
        ]
    )

    trace = fig.data[0]
    assert list(trace.error_y.array) == pytest.approx([0.15, 0.08])
    assert list(trace.error_y.arrayminus) == pytest.approx([0.1, 0.15])
    assert list(trace.text) == ["p=0.010 *", "p=0.200"]


def test_attribution_panel_renders_when_no_data(
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = run_dashboard_panel("attribution", api_client, world, monkeypatch)
    assert not at.exception, at.exception


def test_attribution_panel_with_seeded_data(
    api_client: TestClient,
    world: _World,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_attributions(session, world)
    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/solutions",
        headers={"X-API-Key": world.alice_key},
        json={"solution_id": str(world.acme_solution_id)},
    )
    assert response.status_code == 201, response.text

    at = run_dashboard_panel("attribution", api_client, world, monkeypatch)
    assert not at.exception, at.exception
