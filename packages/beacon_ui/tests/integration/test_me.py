import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_me_returns_user_and_memberships(api_client: TestClient, alice_api_key: str) -> None:
    response = api_client.get("/v1/me", headers={"X-API-Key": alice_api_key})

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "alice@example.com"
    assert isinstance(body["memberships"], list)


def test_me_requires_auth(api_client: TestClient) -> None:
    response = api_client.get("/v1/me")

    assert response.status_code == 401
