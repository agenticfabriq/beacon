"""Reading and revoking team membership.

Granting was one-way: the roster could not be read and a member could not be
removed, so the members screen could only ever show the viewer themselves and
revocation was a database statement. These pin the two missing halves, and the
one refusal that keeps a team recoverable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _World(Protocol):
    acme_team_id: UUID
    alice_id: UUID
    bob_id: UUID
    alice_key: str
    bob_key: str


def _roster(api_client: TestClient, world: _World) -> list[dict[str, str]]:
    response = api_client.get(
        f"/v1/teams/{world.acme_team_id}/members", headers={"X-API-Key": world.alice_key}
    )
    assert response.status_code == 200, response.text
    members: list[dict[str, str]] = response.json()["members"]
    return members


def test_the_roster_lists_everyone_not_just_the_caller(
    api_client: TestClient, world: _World
) -> None:
    """The whole finding: the members screen used to show only your own memberships."""
    members = _roster(api_client, world)

    ids = {row["user_id"] for row in members}
    assert str(world.alice_id) in ids
    assert str(world.bob_id) in ids


def test_the_roster_names_people_rather_than_ids(api_client: TestClient, world: _World) -> None:
    members = _roster(api_client, world)

    assert all(row["email"] for row in members)
    assert all(row["role"] for row in members)


def test_a_member_can_be_removed(api_client: TestClient, world: _World) -> None:
    response = api_client.delete(
        f"/v1/teams/{world.acme_team_id}/members/{world.bob_id}",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 204
    assert str(world.bob_id) not in {row["user_id"] for row in _roster(api_client, world)}


def test_removing_a_member_revokes_their_access(api_client: TestClient, world: _World) -> None:
    """A roster entry that outlives the permission would be decoration."""
    api_client.delete(
        f"/v1/teams/{world.acme_team_id}/members/{world.bob_id}",
        headers={"X-API-Key": world.alice_key},
    )

    response = api_client.get(
        f"/v1/teams/{world.acme_team_id}/solutions", headers={"X-API-Key": world.bob_key}
    )

    assert response.status_code == 403


def test_the_last_admin_cannot_be_removed(api_client: TestClient, world: _World) -> None:
    """A team with no admin can never grant access again."""
    response = api_client.delete(
        f"/v1/teams/{world.acme_team_id}/members/{world.alice_id}",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 409
    assert "last team admin" in response.json()["detail"]


def test_an_admin_can_be_removed_once_another_exists(api_client: TestClient, world: _World) -> None:
    api_client.post(
        f"/v1/teams/{world.acme_team_id}/members",
        headers={"X-API-Key": world.alice_key},
        json={"user_email": "bob@example.com", "role": "team_admin"},
    )

    response = api_client.delete(
        f"/v1/teams/{world.acme_team_id}/members/{world.alice_id}",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 204


def test_removing_someone_who_is_not_a_member_is_a_404(
    api_client: TestClient, world: _World
) -> None:
    response = api_client.delete(
        f"/v1/teams/{world.acme_team_id}/members/{uuid4()}",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 404


def test_a_non_admin_cannot_read_the_roster(api_client: TestClient, world: _World) -> None:
    response = api_client.get(
        f"/v1/teams/{world.acme_team_id}/members", headers={"X-API-Key": world.bob_key}
    )

    assert response.status_code == 403


def test_a_non_admin_cannot_remove_anyone(api_client: TestClient, world: _World) -> None:
    response = api_client.delete(
        f"/v1/teams/{world.acme_team_id}/members/{world.alice_id}",
        headers={"X-API-Key": world.bob_key},
    )

    assert response.status_code == 403
