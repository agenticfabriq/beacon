"""The web UI's endpoint manifest, held against the API it is served with.

The UI declares every path it touches in one ENDPOINTS block. These tests parse
that block out of the shipped HTML and check each entry against the live
OpenAPI schema — so a UI referencing an endpoint that does not exist, or an
endpoint renamed out from under the UI, fails in CI. This replaces the
Streamlit AppTest harness as the API-matches-UI audit.
"""

from __future__ import annotations

import re
from importlib.resources import files
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

_ENTRY = re.compile(r'\["(GET|POST|PATCH|DELETE)",\s*"(/v1/[^"]+)"\]')


def _manifest() -> list[tuple[str, str]]:
    page = (files("beacon_ui.webui") / "index.html").read_text(encoding="utf-8")
    block = re.search(r"const ENDPOINTS = \{(.*?)\};", page, re.S)
    assert block, "the UI must declare its endpoints in one ENDPOINTS block"
    entries = _ENTRY.findall(block.group(1))
    assert entries, "the ENDPOINTS block parsed to nothing"
    return [(method, path) for method, path in entries]


def test_every_ui_endpoint_is_served(api_client: TestClient) -> None:
    schema = api_client.get("/openapi.json").json()
    served = {
        (method.upper(), path)
        for path, methods in schema.get("paths", {}).items()
        for method in methods
    }

    missing = [entry for entry in _manifest() if entry not in served]
    assert not missing, f"the UI references endpoints the API does not serve: {missing}"


def test_the_ui_is_served_from_the_api_origin(api_client: TestClient) -> None:
    """Same origin, no CORS: the page and its API come from one process."""
    response = api_client.get("/ui")

    assert response.status_code == 200
    assert "ENDPOINTS" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_the_ui_is_not_api_surface(api_client: TestClient) -> None:
    """/ui is the client, not the API; it must not appear in the schema."""
    schema = api_client.get("/openapi.json").json()

    assert "/ui" not in schema.get("paths", {})
