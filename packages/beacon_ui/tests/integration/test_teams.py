import pytest
from beacon_iam.auth.api_key import generate_api_key, hash_api_key
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.api_keys import ApiKeyRepo
from beacon_storage.repository.memberships import MembershipRepo
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def admin_api_key(session: Session) -> str:
    user = UserService(session).upsert_from_oidc(
        OidcClaims(subject="admin", email="admin@example.com", name="Admin")
    )
    MembershipRepo(session).grant(
        user_id=user.id,
        scope_kind=ScopeKind.GLOBAL,
        scope_id=user.id,
        role=Role.BEACON_ADMIN,
    )
    key = generate_api_key(prefix="bcn_test")
    ApiKeyRepo(session).create(user_id=user.id, key_hash=hash_api_key(key), label="admin")
    session.commit()
    return key


def test_admin_can_create_team(api_client: TestClient, admin_api_key: str) -> None:
    response = api_client.post(
        "/v1/teams",
        json={"name": "acme", "description": "ACME"},
        headers={"X-API-Key": admin_api_key},
    )

    assert response.status_code == 201, response.text
    assert response.json()["name"] == "acme"


def test_non_admin_cannot_create_team(api_client: TestClient, alice_api_key: str) -> None:
    response = api_client.post(
        "/v1/teams",
        json={"name": "fail"},
        headers={"X-API-Key": alice_api_key},
    )

    assert response.status_code == 403


def test_list_teams_returns_visible_teams(api_client: TestClient, admin_api_key: str) -> None:
    api_client.post("/v1/teams", json={"name": "a"}, headers={"X-API-Key": admin_api_key})
    api_client.post("/v1/teams", json={"name": "b"}, headers={"X-API-Key": admin_api_key})

    response = api_client.get("/v1/teams", headers={"X-API-Key": admin_api_key})

    assert response.status_code == 200
    names = sorted(team["name"] for team in response.json())
    assert "a" in names and "b" in names
