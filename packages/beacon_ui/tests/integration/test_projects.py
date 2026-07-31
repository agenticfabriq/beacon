from uuid import UUID

import pytest
from beacon_iam.auth.api_key import generate_api_key, hash_api_key
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.teams import TeamService
from beacon_iam.service.users import UserService
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.api_keys import ApiKeyRepo
from beacon_storage.repository.memberships import MembershipRepo
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _make_key(session: Session, email: str, name: str) -> tuple[UUID, str]:
    user = UserService(session).upsert_from_oidc(OidcClaims(subject=email, email=email, name=name))
    key = generate_api_key(prefix="bcn_test")
    ApiKeyRepo(session).create(user_id=user.id, key_hash=hash_api_key(key), label="test")
    session.commit()
    return user.id, key


@pytest.fixture
def team_and_members(session: Session) -> dict[str, str]:
    admin_id, admin_key = _make_key(session, "admin2@o.com", "Admin")
    MembershipRepo(session).grant(
        user_id=admin_id,
        scope_kind=ScopeKind.GLOBAL,
        scope_id=admin_id,
        role=Role.BEACON_ADMIN,
    )
    team = TeamService(session).create(actor_id=admin_id, name="acme")

    member_id, member_key = _make_key(session, "member@o.com", "Member")
    MembershipRepo(session).grant(
        user_id=member_id,
        scope_kind=ScopeKind.TEAM,
        scope_id=team.id,
        role=Role.TEAM_MEMBER,
    )
    _outsider_id, outsider_key = _make_key(session, "outsider@o.com", "Outsider")
    session.commit()
    return {
        "team_id": str(team.id),
        "admin_key": admin_key,
        "member_key": member_key,
        "outsider_key": outsider_key,
    }


def test_team_member_creates_project(
    api_client: TestClient, team_and_members: dict[str, str]
) -> None:
    team_id = team_and_members["team_id"]

    response = api_client.post(
        f"/v1/projects?team_id={team_id}",
        json={"name": "schema-linker", "description": "v3.2"},
        headers={"X-API-Key": team_and_members["member_key"]},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "schema-linker"
    assert body["team_id"] == team_id


def test_outsider_cannot_create_project(
    api_client: TestClient, team_and_members: dict[str, str]
) -> None:
    team_id = team_and_members["team_id"]

    response = api_client.post(
        f"/v1/projects?team_id={team_id}",
        json={"name": "x"},
        headers={"X-API-Key": team_and_members["outsider_key"]},
    )

    assert response.status_code == 403


def test_list_projects_filtered_by_rls(
    api_client: TestClient, team_and_members: dict[str, str]
) -> None:
    team_id = team_and_members["team_id"]
    api_client.post(
        f"/v1/projects?team_id={team_id}",
        json={"name": "p1"},
        headers={"X-API-Key": team_and_members["member_key"]},
    )

    response_out = api_client.get(
        f"/v1/projects?team_id={team_id}",
        headers={"X-API-Key": team_and_members["outsider_key"]},
    )
    assert response_out.status_code == 403

    response_in = api_client.get(
        f"/v1/projects?team_id={team_id}",
        headers={"X-API-Key": team_and_members["member_key"]},
    )
    assert response_in.status_code == 200
    assert any(project["name"] == "p1" for project in response_in.json())
