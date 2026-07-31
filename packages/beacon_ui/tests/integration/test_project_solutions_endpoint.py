from uuid import UUID

import pytest
from beacon_storage.models.solutions import Solution
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _World:
    acme_team_id: UUID
    globex_team_id: UUID
    chat_to_data_id: UUID
    alice_id: UUID
    carol_id: UUID
    alice_key: str


def _ensure_solution(
    session: Session,
    team_id: UUID,
    created_by: UUID,
    *,
    solution_id: str = "dummy-sut",
) -> Solution:
    from beacon_storage.repository.solutions import SolutionRepo

    repo = SolutionRepo(session)
    existing = repo.get_by_team_and_solution(team_id, solution_id, "0.1")
    if existing is not None:
        return existing
    return repo.create(
        team_id=team_id,
        solution_id=solution_id,
        version="0.1",
        owner_team=team_id,
        summary=solution_id,
        supported_modes=["EVAL"],
        layers=[],
        created_by=created_by,
    )


def test_owner_adds_solution_to_project(
    api_client: TestClient, world: _World, session: Session
) -> None:
    sut = _ensure_solution(session, world.acme_team_id, world.alice_id)
    session.commit()

    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/solutions",
        headers={"X-API-Key": world.alice_key},
        json={"solution_id": str(sut.id)},
    )

    assert response.status_code == 201, response.text
    assert response.json()["solution_id"] == str(sut.id)


def test_cannot_add_solution_from_different_team(
    api_client: TestClient, world: _World, session: Session
) -> None:
    sut = _ensure_solution(
        session,
        world.globex_team_id,
        world.carol_id,
        solution_id="globex-only-sut",
    )
    session.commit()

    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/solutions",
        headers={"X-API-Key": world.alice_key},
        json={"solution_id": str(sut.id)},
    )

    assert response.status_code == 400
    assert "team" in response.json()["detail"].lower()


def test_delete_unlinks_solution(api_client: TestClient, world: _World, session: Session) -> None:
    sut = _ensure_solution(
        session,
        world.acme_team_id,
        world.alice_id,
        solution_id="to-remove",
    )
    session.commit()

    attach_response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/solutions",
        headers={"X-API-Key": world.alice_key},
        json={"solution_id": str(sut.id)},
    )
    assert attach_response.status_code == 201, attach_response.text

    response = api_client.delete(
        f"/v1/projects/{world.chat_to_data_id}/solutions/{sut.id}",
        headers={"X-API-Key": world.alice_key},
    )
    assert response.status_code == 204

    list_response = api_client.get(
        f"/v1/projects/{world.chat_to_data_id}/solutions",
        headers={"X-API-Key": world.alice_key},
    )
    assert sut.id not in [UUID(row["solution_id"]) for row in list_response.json()]
