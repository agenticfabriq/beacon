import pytest
from beacon_ui.api.app import create_app
from beacon_ui.api.config import ApiConfig


def test_config_reads_beacon_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACON_DATABASE_URL", "postgresql+psycopg://example/db")

    config = ApiConfig()

    assert config.database_url == "postgresql+psycopg://example/db"
    assert config.api_key_prefix == "bcn_dev"


def test_create_app_sets_openapi_metadata() -> None:
    app = create_app()

    assert app.openapi()["info"] == {"title": "Beacon API", "version": "0.2.0"}


def test_openapi_documents_endpoint_roles() -> None:
    schema = create_app().openapi()

    expected_roles = {
        ("get", "/v1/me"): ["authenticated"],
        ("post", "/v1/teams"): ["beacon_admin"],
        ("get", "/v1/teams"): ["authenticated"],
        ("post", "/v1/projects"): ["beacon_admin", "team_admin", "team_member"],
        ("get", "/v1/projects"): ["beacon_admin", "viewer", "team_admin", "team_member"],
        ("post", "/v1/projects/{project_id}/members"): [
            "beacon_admin",
            "team_admin",
            "project_owner",
        ],
        ("post", "/v1/auth/oidc/exchange"): ["public"],
        ("post", "/v1/auth/password/login"): ["public"],
        ("get", "/v1/auth/oidc/start"): ["public"],
        ("get", "/v1/auth/oidc/callback"): ["public"],
    }

    for (method, path), roles in expected_roles.items():
        assert schema["paths"][path][method]["x-required-roles"] == roles
