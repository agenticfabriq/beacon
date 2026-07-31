from typing import Protocol
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _World(Protocol):
    chat_to_data_id: object
    acme_team_id: object
    alice_key: str
    bob_key: str


def test_owner_can_pin_baseline(api_client: TestClient, world: _World) -> None:
    baseline = str(uuid4())

    response = api_client.patch(
        f"/v1/projects/{world.chat_to_data_id}/settings",
        headers={"X-API-Key": world.alice_key},
        json={"baseline_run_id": baseline},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["baseline_run_id"] == baseline


def test_owner_can_update_gate_policy(api_client: TestClient, world: _World) -> None:
    policy = {"mode": "block", "min_pass_at_3": 0.6, "min_delta": -0.05}

    response = api_client.patch(
        f"/v1/projects/{world.chat_to_data_id}/settings",
        headers={"X-API-Key": world.alice_key},
        json={"gate_policy": policy},
    )

    assert response.status_code == 200, response.text
    assert response.json()["gate_policy"] == policy


def test_contributor_cannot_update_settings(api_client: TestClient, world: _World) -> None:
    response = api_client.patch(
        f"/v1/projects/{world.chat_to_data_id}/settings",
        headers={"X-API-Key": world.bob_key},
        json={"gate_policy": {"mode": "warn"}},
    )

    assert response.status_code == 403


def test_partial_update_keeps_other_fields(api_client: TestClient, world: _World) -> None:
    first_response = api_client.patch(
        f"/v1/projects/{world.chat_to_data_id}/settings",
        headers={"X-API-Key": world.alice_key},
        json={"gate_policy": {"mode": "warn"}},
    )
    assert first_response.status_code == 200, first_response.text

    new_baseline = str(uuid4())
    response = api_client.patch(
        f"/v1/projects/{world.chat_to_data_id}/settings",
        headers={"X-API-Key": world.alice_key},
        json={"baseline_run_id": new_baseline},
    )

    assert response.status_code == 200, response.text
    assert response.json()["gate_policy"] == {"mode": "warn"}
    assert response.json()["baseline_run_id"] == new_baseline


def test_owner_can_archive_project(api_client: TestClient, world: _World) -> None:
    response = api_client.delete(
        f"/v1/projects/{world.chat_to_data_id}",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 204, response.text

    list_response = api_client.get(
        "/v1/projects",
        headers={"X-API-Key": world.alice_key},
        params={"team_id": str(world.acme_team_id)},
    )
    assert list_response.status_code == 200, list_response.text
    project_ids = [project["id"] for project in list_response.json()]
    assert str(world.chat_to_data_id) not in project_ids
