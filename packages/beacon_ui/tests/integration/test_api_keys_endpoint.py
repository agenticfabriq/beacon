"""The key lifecycle, and the property that made its absence a security hole.

A key you cannot enumerate is a credential you do not know you have; a key you
cannot revoke is one you can never take back. The test that matters is the one
that uses a revoked key and gets 401.
"""

from typing import Protocol
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _World(Protocol):
    alice_key: str
    bob_key: str


def _create(client: TestClient, key: str, label: str) -> dict[str, str]:
    response = client.post("/v1/me/api-keys", headers={"X-API-Key": key}, json={"label": label})
    assert response.status_code == 201, response.text
    body: dict[str, str] = response.json()
    return body


def test_a_created_key_authenticates(api_client: TestClient, world: _World) -> None:
    created = _create(api_client, world.alice_key, "ci")

    response = api_client.get("/v1/me", headers={"X-API-Key": created["api_key"]})

    assert response.status_code == 200
    assert response.json()["user"]["id"]


def test_a_revoked_key_stops_authenticating(api_client: TestClient, world: _World) -> None:
    """The whole point: before this endpoint existed, a leaked key was permanent."""
    created = _create(api_client, world.alice_key, "leaked")
    assert api_client.get("/v1/me", headers={"X-API-Key": created["api_key"]}).status_code == 200

    revoked = api_client.delete(
        f"/v1/me/api-keys/{created['id']}", headers={"X-API-Key": world.alice_key}
    )

    assert revoked.status_code == 204
    assert api_client.get("/v1/me", headers={"X-API-Key": created["api_key"]}).status_code == 401


def test_the_listing_never_carries_key_material(api_client: TestClient, world: _World) -> None:
    created = _create(api_client, world.alice_key, "ci")

    response = api_client.get("/v1/me/api-keys", headers={"X-API-Key": world.alice_key})

    assert response.status_code == 200
    body = response.text
    assert created["api_key"] not in body
    assert "key_hash" not in body
    assert "api_key" not in body


def test_a_created_key_appears_in_the_listing(api_client: TestClient, world: _World) -> None:
    created = _create(api_client, world.alice_key, "findable")

    keys = api_client.get("/v1/me/api-keys", headers={"X-API-Key": world.alice_key}).json()["keys"]

    assert created["id"] in [key["id"] for key in keys]
    assert "findable" in [key["label"] for key in keys]


def test_a_revoked_key_leaves_the_listing(api_client: TestClient, world: _World) -> None:
    created = _create(api_client, world.alice_key, "temporary")
    api_client.delete(f"/v1/me/api-keys/{created['id']}", headers={"X-API-Key": world.alice_key})

    keys = api_client.get("/v1/me/api-keys", headers={"X-API-Key": world.alice_key}).json()["keys"]

    assert created["id"] not in [key["id"] for key in keys]


def test_one_user_cannot_see_another_users_keys(api_client: TestClient, world: _World) -> None:
    created = _create(api_client, world.alice_key, "alice-only")

    keys = api_client.get("/v1/me/api-keys", headers={"X-API-Key": world.bob_key}).json()["keys"]

    assert created["id"] not in [key["id"] for key in keys]


def test_one_user_cannot_revoke_another_users_key(api_client: TestClient, world: _World) -> None:
    """Ownership is enforced in the repository, so no route can forget it."""
    created = _create(api_client, world.alice_key, "alice-only")

    response = api_client.delete(
        f"/v1/me/api-keys/{created['id']}", headers={"X-API-Key": world.bob_key}
    )

    assert response.status_code == 404
    assert api_client.get("/v1/me", headers={"X-API-Key": created["api_key"]}).status_code == 200


def test_revoking_an_unknown_key_is_a_404(api_client: TestClient, world: _World) -> None:
    response = api_client.delete(
        f"/v1/me/api-keys/{uuid4()}", headers={"X-API-Key": world.alice_key}
    )

    assert response.status_code == 404


def test_revoking_twice_is_a_404_the_second_time(api_client: TestClient, world: _World) -> None:
    created = _create(api_client, world.alice_key, "twice")
    api_client.delete(f"/v1/me/api-keys/{created['id']}", headers={"X-API-Key": world.alice_key})

    second = api_client.delete(
        f"/v1/me/api-keys/{created['id']}", headers={"X-API-Key": world.alice_key}
    )

    assert second.status_code == 404


def test_a_key_needs_a_label(api_client: TestClient, world: _World) -> None:
    """The label is the only handle on a key, so an empty one is useless."""
    response = api_client.post(
        "/v1/me/api-keys", headers={"X-API-Key": world.alice_key}, json={"label": ""}
    )

    assert response.status_code == 422


def test_creating_a_key_requires_authentication(api_client: TestClient) -> None:
    response = api_client.post("/v1/me/api-keys", json={"label": "anonymous"})

    assert response.status_code in (401, 403, 503)


def test_using_a_key_records_when_it_was_last_used(api_client: TestClient, world: _World) -> None:
    """Deciding what to revoke needs to show which keys are still in play."""
    created = _create(api_client, world.alice_key, "used")
    api_client.get("/v1/me", headers={"X-API-Key": created["api_key"]})

    keys = api_client.get("/v1/me/api-keys", headers={"X-API-Key": world.alice_key}).json()["keys"]
    entry = next(key for key in keys if key["id"] == created["id"])

    assert entry["last_used_at"] is not None
