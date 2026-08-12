from typing import Protocol
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _World(Protocol):
    acme_team_id: object
    alice_id: object
    bob_id: object
    carol_id: object
    alice_key: str
    bob_key: str


def test_team_admin_can_add_member(api_client: TestClient, world: _World) -> None:
    response = api_client.post(
        f"/v1/teams/{world.acme_team_id}/members",
        headers={"X-API-Key": world.alice_key},
        json={"user_email": "carol@example.com", "role": "team_member"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "team_member"
    assert body["scope_kind"] == "team"


def test_non_admin_forbidden(api_client: TestClient, world: _World) -> None:
    response = api_client.post(
        f"/v1/teams/{world.acme_team_id}/members",
        headers={"X-API-Key": world.bob_key},
        json={"user_email": "carol@example.com", "role": "team_member"},
    )

    assert response.status_code == 403


def test_unknown_user_email_is_an_invite_not_an_error(
    api_client: TestClient, world: _World
) -> None:
    """The account is created now and links to their identity at first login."""
    response = api_client.post(
        f"/v1/teams/{world.acme_team_id}/members",
        headers={"X-API-Key": world.alice_key},
        json={"user_email": "paulina@example.com", "role": "team_member"},
    )

    assert response.status_code == 201, response.text

    roster = api_client.get(
        f"/v1/teams/{world.acme_team_id}/members",
        headers={"X-API-Key": world.alice_key},
    ).json()
    emails = [m["email"] for m in (roster if isinstance(roster, list) else roster["members"])]
    assert "paulina@example.com" in emails


def test_unknown_team_returns_404(api_client: TestClient, world: _World) -> None:
    response = api_client.post(
        f"/v1/teams/{uuid4()}/members",
        headers={"X-API-Key": world.alice_key},
        json={"user_email": "bob@example.com", "role": "team_member"},
    )

    assert response.status_code == 404


def test_admin_can_issue_a_key_for_a_member(api_client: TestClient, world: _World) -> None:
    """An invited person's first credential comes from the roster."""
    response = api_client.post(
        f"/v1/teams/{world.acme_team_id}/members/{world.bob_id}/api-keys",
        headers={"X-API-Key": world.alice_key},
        json={"label": "issued-for-bob"},
    )

    assert response.status_code == 201, response.text
    issued = response.json()["api_key"]

    me = api_client.get("/v1/me", headers={"X-API-Key": issued})
    assert me.status_code == 200, me.text
    assert me.json()["user"]["email"] == "bob@example.com"


def test_a_member_cannot_issue_keys(api_client: TestClient, world: _World) -> None:
    response = api_client.post(
        f"/v1/teams/{world.acme_team_id}/members/{world.alice_id}/api-keys",
        headers={"X-API-Key": world.bob_key},
        json={"label": "sneaky"},
    )

    assert response.status_code == 403


def test_issuing_for_a_non_member_is_a_404(api_client: TestClient, world: _World) -> None:
    response = api_client.post(
        f"/v1/teams/{world.acme_team_id}/members/{world.carol_id}/api-keys",
        headers={"X-API-Key": world.alice_key},
        json={"label": "wrong-team"},
    )

    assert response.status_code == 404
