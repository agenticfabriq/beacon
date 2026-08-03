"""POST + GET /v1/teams/{team_id}/suites."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from beacon_storage.models.tenancy import Team
    from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_create_suite(
    api_client: TestClient,
    alice_api_key: str,
    alice_team_membership: Team,
) -> None:
    response = api_client.post(
        f"/v1/teams/{alice_team_membership.id}/suites",
        json={
            "name": "curated-50",
            "description": "d",
            "method": "manual",
            "metadata": {"source": "handpicked"},
        },
        headers={"X-API-Key": alice_api_key},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "curated-50"
    assert body["method"] == "manual"
    assert body["metadata"] == {"source": "handpicked"}
    assert body["team_id"] == str(alice_team_membership.id)


def test_create_duplicate_suite_returns_409(
    api_client: TestClient,
    alice_api_key: str,
    alice_team_membership: Team,
) -> None:
    first_response = api_client.post(
        f"/v1/teams/{alice_team_membership.id}/suites",
        json={"name": "dupe", "method": "manual"},
        headers={"X-API-Key": alice_api_key},
    )
    assert first_response.status_code == 201, first_response.text

    response = api_client.post(
        f"/v1/teams/{alice_team_membership.id}/suites",
        json={"name": "dupe", "method": "manual"},
        headers={"X-API-Key": alice_api_key},
    )

    assert response.status_code == 409


def test_list_suites(
    api_client: TestClient,
    alice_api_key: str,
    alice_team_membership: Team,
) -> None:
    for name in ("b", "a"):
        response = api_client.post(
            f"/v1/teams/{alice_team_membership.id}/suites",
            json={"name": name, "method": "manual"},
            headers={"X-API-Key": alice_api_key},
        )
        assert response.status_code == 201, response.text

    response = api_client.get(
        f"/v1/teams/{alice_team_membership.id}/suites",
        headers={"X-API-Key": alice_api_key},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [suite["name"] for suite in body] == ["a", "b"]


def test_invalid_method_rejected(
    api_client: TestClient,
    alice_api_key: str,
    alice_team_membership: Team,
) -> None:
    response = api_client.post(
        f"/v1/teams/{alice_team_membership.id}/suites",
        json={"name": "bad", "method": "made_up"},
        headers={"X-API-Key": alice_api_key},
    )

    assert response.status_code == 422
