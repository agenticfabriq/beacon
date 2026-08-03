"""Every protected P7 endpoint declares required permissions in OpenAPI."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

EXEMPT_PATHS: set[tuple[str, str]] = {
    ("/v1/auth/oidc/callback", "get"),
    ("/v1/auth/oidc/exchange", "post"),
    ("/v1/auth/oidc/start", "get"),
    ("/v1/auth/password/login", "post"),
    ("/v1/leaderboards/cost", "get"),
    ("/v1/leaderboards/latency", "get"),
    ("/v1/me", "get"),
    ("/v1/projects", "get"),
    ("/v1/projects", "post"),
    ("/v1/projects/{project_id}/members", "post"),
    ("/v1/registry/items", "get"),
    ("/v1/teams", "get"),
    ("/v1/teams", "post"),
    ("/v1/traces", "post"),
}


def test_every_protected_endpoint_has_required_permissions(api_client: TestClient) -> None:
    schema = api_client.get("/openapi.json").json()
    missing: list[str] = []

    for path, methods in schema.get("paths", {}).items():
        for method, operation in methods.items():
            if method not in {"delete", "get", "patch", "post", "put"}:
                continue
            if (path, method) in EXEMPT_PATHS:
                continue
            if "x-required-permissions" not in operation:
                missing.append(f"{method.upper()} {path}")

    assert not missing, "Endpoints missing @requires(): " + ", ".join(missing)


def test_team_members_endpoint_requires_team_manage(api_client: TestClient) -> None:
    schema = api_client.get("/openapi.json").json()
    operation = schema["paths"]["/v1/teams/{team_id}/members"]["post"]

    assert "team.manage" in operation["x-required-permissions"]


def test_project_settings_endpoint_requires_project_manage(api_client: TestClient) -> None:
    schema = api_client.get("/openapi.json").json()
    operation = schema["paths"]["/v1/projects/{project_id}/settings"]["patch"]

    assert "project.manage" in operation["x-required-permissions"]


def test_runs_endpoint_requires_run_eval(api_client: TestClient) -> None:
    schema = api_client.get("/openapi.json").json()
    operation = schema["paths"]["/v1/projects/{project_id}/runs"]["post"]

    assert "project.run_eval" in operation["x-required-permissions"]
