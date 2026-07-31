from typing import Protocol
from uuid import UUID

import pytest
from beacon_storage.models.solutions import Solution
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _World(Protocol):
    acme_team_id: UUID
    globex_team_id: UUID
    chat_to_data_id: UUID
    alice_id: UUID
    carol_id: UUID
    alice_key: str
    bob_key: str
    carol_key: str


def _ensure_solution(session: Session, world: _World) -> Solution:
    from beacon_storage.repository.solutions import SolutionRepo

    repo = SolutionRepo(session)
    existing = repo.get_by_team_and_solution(world.acme_team_id, "dummy-runner", "0.1")
    if existing is not None:
        return existing
    return repo.create(
        team_id=world.acme_team_id,
        solution_id="dummy-runner",
        version="0.1",
        owner_team=world.acme_team_id,
        summary="Dummy runner",
        supported_modes=["EVAL", "PR_GATE"],
        layers=[],
        created_by=world.alice_id,
    )


def _setup_runnable(api_client: TestClient, session: Session, world: _World) -> tuple[str, str]:
    sut = _ensure_solution(session, world)
    session.commit()

    attach_response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/solutions",
        headers={"X-API-Key": world.alice_key},
        json={"solution_id": str(sut.id)},
    )
    assert attach_response.status_code in (200, 201), attach_response.text

    suite_response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/suites",
        headers={"X-API-Key": world.alice_key},
        json={"name": "tiny-run-suite", "kind": "manual", "item_ids": []},
    )
    assert suite_response.status_code in (201, 409), suite_response.text
    if suite_response.status_code == 409:
        list_response = api_client.get(
            f"/v1/projects/{world.chat_to_data_id}/suites",
            headers={"X-API-Key": world.alice_key},
        )
        suite_id = next(
            suite["suite_id"] for suite in list_response.json() if suite["name"] == "tiny-run-suite"
        )
    else:
        suite_id = suite_response.json()["suite_id"]
    return str(sut.id), suite_id


def test_kick_off_run_returns_202(api_client: TestClient, world: _World, session: Session) -> None:
    solution_id, suite_id = _setup_runnable(api_client, session, world)

    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/runs",
        headers={"X-API-Key": world.alice_key},
        json={
            "solution_id": solution_id,
            "suite_id": suite_id,
            "mode": "EVAL",
            "config": {"k": 1},
        },
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["run_id"]
    assert body["solution_id"] == solution_id
    assert body["suite_id"] == suite_id
    assert body["status"] in ("queued", "running")


def test_contributor_can_kick_off_run(
    api_client: TestClient, world: _World, session: Session
) -> None:
    solution_id, suite_id = _setup_runnable(api_client, session, world)

    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/runs",
        headers={"X-API-Key": world.bob_key},
        json={
            "solution_id": solution_id,
            "suite_id": suite_id,
            "mode": "EVAL",
            "config": {"k": 1},
        },
    )

    assert response.status_code == 202, response.text


def test_outsider_cannot_kick_off_run(
    api_client: TestClient, world: _World, session: Session
) -> None:
    solution_id, suite_id = _setup_runnable(api_client, session, world)

    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/runs",
        headers={"X-API-Key": world.carol_key},
        json={
            "solution_id": solution_id,
            "suite_id": suite_id,
            "mode": "EVAL",
            "config": {"k": 1},
        },
    )

    assert response.status_code == 403


def test_invalid_mode_rejected(api_client: TestClient, world: _World, session: Session) -> None:
    solution_id, suite_id = _setup_runnable(api_client, session, world)

    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/runs",
        headers={"X-API-Key": world.alice_key},
        json={
            "solution_id": solution_id,
            "suite_id": suite_id,
            "mode": "NOT_A_MODE",
            "config": {},
        },
    )

    assert response.status_code == 422


def test_get_run_status(api_client: TestClient, world: _World, session: Session) -> None:
    solution_id, suite_id = _setup_runnable(api_client, session, world)
    create_response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/runs",
        headers={"X-API-Key": world.alice_key},
        json={
            "solution_id": solution_id,
            "suite_id": suite_id,
            "mode": "EVAL",
            "config": {},
        },
    )
    run_id = create_response.json()["run_id"]

    response = api_client.get(
        f"/v1/projects/{world.chat_to_data_id}/runs/{run_id}",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == run_id
    assert body["suite_id"] == suite_id
    assert "status" in body
    assert "summary" in body


def test_list_runs(api_client: TestClient, world: _World, session: Session) -> None:
    solution_id, suite_id = _setup_runnable(api_client, session, world)
    create_response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/runs",
        headers={"X-API-Key": world.alice_key},
        json={
            "solution_id": solution_id,
            "suite_id": suite_id,
            "mode": "EVAL",
            "config": {},
        },
    )
    run_id = create_response.json()["run_id"]

    response = api_client.get(
        f"/v1/projects/{world.chat_to_data_id}/runs",
        headers={"X-API-Key": world.alice_key},
        params={"solution_id": solution_id, "suite_id": suite_id, "status": "queued"},
    )

    assert response.status_code == 200, response.text
    run_ids = [row["run_id"] for row in response.json()]
    assert run_id in run_ids
