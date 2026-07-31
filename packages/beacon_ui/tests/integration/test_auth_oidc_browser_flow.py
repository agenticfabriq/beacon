"""Browser auth-code flow: /start -> IdP -> /callback -> API key issued."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from beacon_iam.auth.oidc import OidcClaims

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture
def _mock_oidc_verifier() -> Iterator[Mock]:
    with patch("beacon_iam.auth.oidc.OidcVerifier.verify_id_token") as mock:
        mock.return_value = OidcClaims(
            subject="user-cli",
            email="cli@example.com",
            name="CLI User",
        )
        yield mock


@pytest.fixture
def _stub_token_exchange() -> Iterator[Mock]:
    with patch("beacon_iam.auth.oidc.OidcAuthCodeClient.exchange_code_for_id_token") as mock:
        mock.return_value = "stub-id-token"
        yield mock


def test_start_returns_redirect_to_idp(api_client: TestClient) -> None:
    response = api_client.get("/v1/auth/oidc/start", follow_redirects=False)

    assert response.status_code in (302, 307)
    location = response.headers["location"]
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    assert "beacon_oidc_state" in response.cookies
    assert query.get("response_type") == ["code"]
    assert query.get("scope") == ["openid email profile"]
    assert "state" in query
    assert "redirect_uri" in query


@patch("beacon_iam.auth.oidc.OidcAuthCodeClient.exchange_code_for_id_token")
@patch("beacon_iam.auth.oidc.OidcVerifier.verify_id_token")
def test_callback_exchanges_code_and_issues_api_key(
    mock_verify: Mock,
    mock_exchange: Mock,
    api_client: TestClient,
) -> None:
    mock_exchange.return_value = "stub-id-token"
    mock_verify.return_value = OidcClaims(
        subject="user1",
        email="user1@example.com",
        name="User One",
    )
    start = api_client.get("/v1/auth/oidc/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    response = api_client.get(
        f"/v1/auth/oidc/callback?code=abc&state={state}",
        cookies=start.cookies,
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    assert "beacon_api_key_once" in response.cookies


def test_callback_rejects_mismatched_state(api_client: TestClient) -> None:
    response = api_client.get(
        "/v1/auth/oidc/callback?code=abc&state=wrong",
        cookies={"beacon_oidc_state": "real"},
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_start_rejects_non_loopback_cli_callback(api_client: TestClient) -> None:
    response = api_client.get(
        "/v1/auth/oidc/start?cli_callback=https://evil.example.com/x&cli_state=anything",
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_callback_routes_cli_flow_to_localhost(
    api_client: TestClient,
    _mock_oidc_verifier: Mock,
    _stub_token_exchange: Mock,
) -> None:
    start = api_client.get(
        "/v1/auth/oidc/start?cli_callback=http://127.0.0.1:8765/cb&cli_state=CLISTATE",
        follow_redirects=False,
    )
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    response = api_client.get(
        f"/v1/auth/oidc/callback?code=abc&state={state}",
        cookies=start.cookies,
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("http://127.0.0.1:8765/cb?")
    params = parse_qs(urlparse(location).query)
    assert "api_key" in params
    assert params["state"] == ["CLISTATE"]
    assert "beacon_api_key_once" not in response.cookies
