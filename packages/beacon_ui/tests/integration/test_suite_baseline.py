from typing import Protocol

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _World(Protocol):
    acme_suite_id: object
    acme_run_id: object
    alice_key: str
    bob_key: str


def test_admin_can_pin_baseline(api_client: TestClient, world: _World) -> None:
    response = api_client.patch(
        f"/v1/suites/{world.acme_suite_id}",
        headers={"X-API-Key": world.alice_key},
        json={"baseline_run_id": str(world.acme_run_id)},
    )

    assert response.status_code == 200, response.text
    assert response.json()["baseline_run_id"] == str(world.acme_run_id)


def test_member_cannot_pin_baseline(api_client: TestClient, world: _World) -> None:
    response = api_client.patch(
        f"/v1/suites/{world.acme_suite_id}",
        headers={"X-API-Key": world.bob_key},
        json={"baseline_run_id": str(world.acme_run_id)},
    )

    assert response.status_code == 403


def test_pinning_a_baseline_leaves_it_pinned(api_client: TestClient, world: _World) -> None:
    api_client.patch(
        f"/v1/suites/{world.acme_suite_id}",
        headers={"X-API-Key": world.alice_key},
        json={"baseline_run_id": str(world.acme_run_id)},
    )

    response = api_client.patch(
        f"/v1/suites/{world.acme_suite_id}",
        headers={"X-API-Key": world.alice_key},
        json={},
    )

    assert response.json()["baseline_run_id"] == str(world.acme_run_id)


def test_explicit_null_clears_the_pin(api_client: TestClient, world: _World) -> None:
    api_client.patch(
        f"/v1/suites/{world.acme_suite_id}",
        headers={"X-API-Key": world.alice_key},
        json={"baseline_run_id": str(world.acme_run_id)},
    )

    response = api_client.patch(
        f"/v1/suites/{world.acme_suite_id}",
        headers={"X-API-Key": world.alice_key},
        json={"baseline_run_id": None},
    )

    assert response.status_code == 200, response.text
    assert response.json()["baseline_run_id"] is None
