"""End-to-end auth: OIDC/password login issue API keys, and bearer auth works."""

from unittest.mock import Mock, patch

import pytest
from beacon_iam.auth.oidc import OidcClaims
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@patch("beacon_iam.auth.oidc.OidcVerifier.verify_id_token")
def test_oidc_login_issues_api_key(mock_verify: Mock, api_client: TestClient) -> None:
    mock_verify.return_value = OidcClaims(
        subject="alice-oidc",
        email="alice@example.com",
        name="Alice",
    )

    response = api_client.post(
        "/v1/auth/oidc/exchange",
        json={"id_token": "stub-jwt", "label": "browser-1"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["api_key"].startswith("bcn_")

    me_response = api_client.get("/v1/me", headers={"X-API-Key": body["api_key"]})
    assert me_response.status_code == 200
    assert me_response.json()["user"]["email"] == "alice@example.com"


def test_password_login_with_correct_password(api_client: TestClient, session: Session) -> None:
    from beacon_iam.auth.password import hash_password
    from beacon_storage.repository.users import UserRepo

    UserRepo(session).create(
        email="svc@example.com",
        name="Service",
        password_hash=hash_password("hunter2!"),
    )
    session.commit()

    response = api_client.post(
        "/v1/auth/password/login",
        json={"email": "svc@example.com", "password": "hunter2!", "label": "ci-bot"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["api_key"].startswith("bcn_")


def test_password_login_wrong_password(api_client: TestClient, session: Session) -> None:
    from beacon_iam.auth.password import hash_password
    from beacon_storage.repository.users import UserRepo

    UserRepo(session).create(
        email="svc2@example.com",
        name="Svc",
        password_hash=hash_password("right"),
    )
    session.commit()

    response = api_client.post(
        "/v1/auth/password/login",
        json={"email": "svc2@example.com", "password": "wrong", "label": "x"},
    )

    assert response.status_code == 401


@patch("beacon_iam.auth.oidc.OidcVerifier.verify_id_token")
def test_bearer_token_authenticates_user(mock_verify: Mock, api_client: TestClient) -> None:
    mock_verify.return_value = OidcClaims(
        subject="bearer-oidc",
        email="bearer@example.com",
        name="Bearer",
    )

    response = api_client.get("/v1/me", headers={"Authorization": "Bearer stub-jwt"})

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "bearer@example.com"
