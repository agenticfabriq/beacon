from typing import Protocol
from uuid import UUID

import pytest
from beacon_storage.ids import uuid7
from beacon_storage.models.attribution import Attribution
from beacon_storage.models.runs import HarnessMode
from beacon_storage.models.solutions import Solution
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _World(Protocol):
    acme_team_id: UUID
    acme_suite_id: UUID
    acme_solution_id: UUID
    alice_id: UUID
    alice_key: str


def _seed_attributions(session: Session, world: _World) -> UUID:
    suite = SuiteRepo(session).get(world.acme_suite_id)
    assert suite is not None

    run_repo = RunRepo(session)
    sweep_id = uuid7()
    baseline = run_repo.create(
        team_id=world.acme_team_id,
        suite_id=suite.id,
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
        suite_id=suite.id,
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

    for layer_name, delta, bh_p, token_delta in (
        ("ontology", 0.18, 0.006, -0.12),
        ("retry_loop", -0.04, 0.120, 0.08),
    ):
        session.add(
            Attribution(
                attribution_id=uuid7(),
                sweep_id=sweep_id,
                team_id=world.acme_team_id,
                solution_id=world.acme_solution_id,
                solution_version="0.1",
                suite=suite.name,
                dataset_version="v2",
                layer_name=layer_name,
                methodology="LOO",
                baseline_run_id=baseline.id,
                ablated_run_id=ablated.id,
                pass_at_k_baseline={"3": 0.71},
                pass_at_k_ablated={"3": 0.71 + delta},
                delta_pass_at_k={
                    "3": {
                        "delta": delta,
                        "ci_low": delta - 0.02,
                        "ci_high": delta + 0.02,
                        "p": bh_p / 2,
                    }
                },
                pass_hat_k_baseline={"3": 0.69},
                pass_hat_k_ablated={"3": 0.69 + delta},
                delta_pass_hat_k={
                    "3": {
                        "delta": delta,
                        "ci_low": delta - 0.03,
                        "ci_high": delta + 0.03,
                        "p": bh_p / 2,
                    }
                },
                token_delta_pct=token_delta,
                runtime_delta_pct=None,
                mcnemar_p=bh_p / 2,
                bh_adjusted_p=bh_p,
                ci_low=delta - 0.02,
                ci_high=delta + 0.02,
            )
        )
    session.commit()
    return suite.id


def _ensure_layerless_solution(session: Session, world: _World) -> Solution:
    repo = SolutionRepo(session)
    existing = repo.get_by_team_and_solution(world.acme_team_id, "layerless-api-sut", "0.1")
    if existing is not None:
        return existing
    return repo.create(
        team_id=world.acme_team_id,
        solution_id="layerless-api-sut",
        version="0.1",
        owner_team=world.acme_team_id,
        summary="Layerless API SUT",
        supported_modes=["EVAL"],
        layers=[],
        created_by=world.alice_id,
    )


def test_get_attribution_latest_snapshot(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    suite_id = _seed_attributions(session, world)

    response = api_client.get(
        f"/v1/suites/{suite_id}/attribution",
        headers={"X-API-Key": world.alice_key},
        params={"sut": str(world.acme_solution_id)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["solution_id"] == str(world.acme_solution_id)
    assert body["suite_id"] == str(suite_id)
    assert body["supported"] is True
    assert body["computed_at"] is not None
    assert [row["layer"] for row in body["layers"]] == ["ontology", "retry_loop"]
    assert body["layers"][0]["delta_pass_at_3"] == pytest.approx(0.18)
    assert body["layers"][0]["ci_low"] == pytest.approx(0.16)
    assert body["layers"][0]["ci_high"] == pytest.approx(0.20)
    assert body["layers"][0]["mcnemar_p"] == pytest.approx(0.003)
    assert body["layers"][0]["bh_p"] == pytest.approx(0.006)
    assert body["layers"][0]["median_token_delta_pct"] == pytest.approx(-0.12)


def test_get_attribution_layerless_solution_is_unsupported(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    suite = SuiteRepo(session).get(world.acme_suite_id)
    assert suite is not None
    solution = _ensure_layerless_solution(session, world)
    session.commit()

    response = api_client.get(
        f"/v1/suites/{suite.id}/attribution",
        headers={"X-API-Key": world.alice_key},
        params={"sut": str(solution.id)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["supported"] is False
    assert body["computed_at"] is None
    assert body["layers"] == []
