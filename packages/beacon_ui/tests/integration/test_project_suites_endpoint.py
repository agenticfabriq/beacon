from typing import Protocol
from uuid import UUID

import pytest
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.repository.eval_items import EvalItemRepo
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _World(Protocol):
    acme_team_id: UUID
    chat_to_data_id: UUID
    alice_id: UUID
    alice_key: str
    carol_key: str


def _create_item(session: Session, world: _World, question: str) -> UUID:
    item = EvalItemRepo(session).create(
        tier=EvalItemTier.EXECUTION_CONFIRMED,
        suite="bird_minidev_v2",
        team_id=world.acme_team_id,
        item_input={"question": question},
        gold_answer={"sql": "SELECT 1"},
        item_metadata={"source": "task-7-test"},
        created_by=world.alice_id,
    )
    return item.item_id


def test_create_manual_suite(api_client: TestClient, world: _World, session: Session) -> None:
    item_ids = [
        _create_item(session, world, "Q1"),
        _create_item(session, world, "Q2"),
    ]
    session.commit()

    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/suites",
        headers={"X-API-Key": world.alice_key},
        json={
            "name": "smoke-10",
            "kind": "manual",
            "item_ids": [str(item_id) for item_id in item_ids],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "smoke-10"
    assert body["item_count"] == 2


def test_create_curated_suite(api_client: TestClient, world: _World) -> None:
    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/suites",
        headers={"X-API-Key": world.alice_key},
        json={
            "name": "regression-50",
            "kind": "curated",
            "source_suite": "bird_minidev_v2",
            "target_size": 50,
        },
    )

    assert response.status_code in (200, 201, 400), response.text


def test_viewer_cannot_create_suite(api_client: TestClient, world: _World) -> None:
    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/suites",
        headers={"X-API-Key": world.carol_key},
        json={"name": "x", "kind": "manual", "item_ids": []},
    )

    assert response.status_code == 403


def test_list_suites(api_client: TestClient, world: _World) -> None:
    create_response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/suites",
        headers={"X-API-Key": world.alice_key},
        json={"name": "smoke-list", "kind": "manual", "item_ids": []},
    )
    assert create_response.status_code == 201, create_response.text

    response = api_client.get(
        f"/v1/projects/{world.chat_to_data_id}/suites",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 200, response.text
    names = [suite["name"] for suite in response.json()]
    assert "smoke-list" in names
