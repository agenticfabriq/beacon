"""Full happy path: admin login, team creation, team-admin benchmark creation."""

from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from beacon_iam.auth.oidc import OidcClaims
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.users import UserRepo
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@patch("beacon_iam.auth.oidc.OidcVerifier.verify_id_token")
def test_full_admin_to_member_flow(
    mock_verify: Mock,
    api_client: TestClient,
    session: Session,
) -> None:
    mock_verify.return_value = OidcClaims(
        subject="admin",
        email="admin@example.com",
        name="Admin",
    )
    response = api_client.post(
        "/v1/auth/oidc/exchange",
        json={"id_token": "stub", "label": "admin"},
    )
    assert response.status_code == 201, response.text
    admin_key = response.json()["api_key"]

    admin = UserRepo(session).get_by_email("admin@example.com")
    assert admin is not None
    MembershipRepo(session).grant(
        user_id=admin.id,
        scope_kind=ScopeKind.GLOBAL,
        scope_id=admin.id,
        role=Role.BEACON_ADMIN,
    )
    session.commit()

    response = api_client.post(
        "/v1/teams",
        json={"name": "acme", "description": "ACME"},
        headers={"X-API-Key": admin_key},
    )
    assert response.status_code == 201, response.text
    team_id = response.json()["id"]

    mock_verify.return_value = OidcClaims(
        subject="member",
        email="member@example.com",
        name="Member",
    )
    response = api_client.post(
        "/v1/auth/oidc/exchange",
        json={"id_token": "stub2", "label": "member"},
    )
    assert response.status_code == 201, response.text
    member_key = response.json()["api_key"]
    member = UserRepo(session).get_by_email("member@example.com")
    assert member is not None

    MembershipRepo(session).grant(
        user_id=member.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=UUID(team_id),
        role=Role.TEAM_ADMIN,
    )
    session.commit()

    response = api_client.post(
        f"/v1/teams/{team_id}/suites",
        json={"name": "schema-linker-tuning", "method": "manual"},
        headers={"X-API-Key": member_key},
    )
    assert response.status_code == 201, response.text
    assert response.json()["team_id"] == team_id

    response = api_client.get("/v1/me", headers={"X-API-Key": member_key})
    assert response.status_code == 200, response.text
    body = response.json()
    team_role = next(
        (
            membership
            for membership in body["memberships"]
            if membership["scope_kind"] == "team" and membership["scope_id"] == team_id
        ),
        None,
    )
    assert team_role is not None
    assert team_role["role"] == "team_admin"
