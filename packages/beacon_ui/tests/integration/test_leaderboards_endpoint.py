from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.runs import HarnessMode, ResultStatus, VerdictOutcome
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.suites import SuiteRepo

if TYPE_CHECKING:
    from uuid import UUID

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _World(Protocol):
    acme_team_id: UUID
    globex_team_id: UUID
    chat_to_data_id: UUID
    globex_research_id: UUID
    acme_solution_id: UUID
    chat_to_data_run_id: UUID
    globex_research_run_id: UUID
    alice_id: UUID
    carol_id: UUID
    alice_key: str
    carol_key: str


def _seed_shared_results(session: Session, world: _World) -> None:
    items = EvalItemRepo(session).list_active(suite="bird_minidev_v2", team_id=None)
    assert len(items) >= 3

    repo = ResultRepo(session)
    for idx, item in enumerate(items[:3]):
        repo.create(
            team_id=world.acme_team_id,
            project_id=world.chat_to_data_id,
            run_id=world.chat_to_data_run_id,
            item_id=str(item.item_id),
            attempt_idx=0,
            output={"answer": f"acme-{idx}"},
            output_kind="answer",
            tokens_input=60,
            tokens_output=40,
            runtime_ms=300 + idx * 10,
            status=ResultStatus.COMPLETED,
            outcome=VerdictOutcome.PASS if idx < 2 else VerdictOutcome.FAIL,
            error=None,
        )
        repo.create(
            team_id=world.globex_team_id,
            project_id=world.globex_research_id,
            run_id=world.globex_research_run_id,
            item_id=str(item.item_id),
            attempt_idx=0,
            output={"answer": f"globex-{idx}"},
            output_kind="answer",
            tokens_input=120,
            tokens_output=80,
            runtime_ms=100 + idx * 10,
            status=ResultStatus.COMPLETED,
            outcome=VerdictOutcome.PASS,
            error=None,
        )
    session.commit()


def _seed_private_results(session: Session, world: _World) -> None:
    suite = SuiteRepo(session).create(
        project_id=world.chat_to_data_id,
        team_id=world.acme_team_id,
        name="private_team_suite",
        description="Team-only suite",
        method="manual",
        suite_metadata={},
        created_by=world.alice_id,
    )
    item = EvalItemRepo(session).create(
        tier=EvalItemTier.HUMAN_VERIFIED,
        suite=suite.name,
        team_id=world.acme_team_id,
        dataset_version="v2",
        item_input={"question": "private"},
        gold_answer={"answer": "private"},
        item_metadata={},
        created_by=world.alice_id,
    )
    run = RunRepo(session).create(
        team_id=world.acme_team_id,
        project_id=world.chat_to_data_id,
        solution_id=world.acme_solution_id,
        suite=suite.name,
        dataset_version="v2",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        config={},
        created_by=world.alice_id,
    )
    RunRepo(session).mark_completed(run.id)
    ResultRepo(session).create(
        team_id=world.acme_team_id,
        project_id=world.chat_to_data_id,
        run_id=run.id,
        item_id=str(item.item_id),
        attempt_idx=0,
        output={"answer": "private"},
        output_kind="answer",
        tokens_input=1,
        tokens_output=1,
        runtime_ms=1,
        status=ResultStatus.COMPLETED,
        outcome=VerdictOutcome.PASS,
        error=None,
    )
    session.commit()


def test_cost_leaderboard_returns_cost_adjusted_rows(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    _seed_shared_results(session, world)

    response = api_client.get(
        "/v1/leaderboards/cost",
        headers={"X-API-Key": world.alice_key},
        params={"suite": "bird_minidev_v2"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suite"] == "bird_minidev_v2"
    assert body["metric"] == "cost"
    assert [row["team_name"] for row in body["rows"]] == ["acme", "globex"]
    first = body["rows"][0]
    assert first["pass_at_3"] == pytest.approx(2 / 3)
    assert first["median_tokens"] == pytest.approx(100.0)
    assert first["cost_adjusted_score"] == pytest.approx((2 / 3) / 100)
    assert first["latency_adjusted_score"] is None
    assert first["n_items"] == 3


def test_latency_leaderboard_returns_latency_adjusted_rows(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    _seed_shared_results(session, world)

    response = api_client.get(
        "/v1/leaderboards/latency",
        headers={"X-API-Key": world.alice_key},
        params={"suite": "bird_minidev_v2"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metric"] == "latency"
    assert [row["team_name"] for row in body["rows"]] == ["globex", "acme"]
    first = body["rows"][0]
    assert first["pass_at_3"] == pytest.approx(1.0)
    assert first["median_latency_ms"] == pytest.approx(110.0)
    assert first["latency_adjusted_score"] == pytest.approx(1 / 110)
    assert first["cost_adjusted_score"] is None


def test_leaderboard_includes_cross_team_shared_suite_rows(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    _seed_shared_results(session, world)

    response = api_client.get(
        "/v1/leaderboards/cost",
        headers={"X-API-Key": world.carol_key},
        params={"suite": "bird_minidev_v2"},
    )

    assert response.status_code == 200, response.text
    assert {row["team_name"] for row in response.json()["rows"]} == {"acme", "globex"}


def test_leaderboard_excludes_team_scoped_suites(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    _seed_private_results(session, world)

    response = api_client.get(
        "/v1/leaderboards/cost",
        headers={"X-API-Key": world.alice_key},
        params={"suite": "private_team_suite"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["rows"] == []


def test_leaderboard_limit_and_authentication(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    _seed_shared_results(session, world)

    limited = api_client.get(
        "/v1/leaderboards/cost",
        headers={"X-API-Key": world.alice_key},
        params={"suite": "bird_minidev_v2", "limit": 1},
    )
    unauthenticated = api_client.get(
        "/v1/leaderboards/cost",
        params={"suite": "bird_minidev_v2"},
    )

    assert limited.status_code == 200, limited.text
    assert len(limited.json()["rows"]) == 1
    assert unauthenticated.status_code == 401
