from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx
from streamlit.testing.v1 import AppTest

if TYPE_CHECKING:
    from uuid import UUID

    import pytest
    from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]


class DashboardWorld(Protocol):
    alice_key: str
    acme_team_id: UUID
    chat_to_data_id: UUID


class DashboardClientTransport:
    def __init__(self, api_client: TestClient, api_base: str) -> None:
        self._api_client = api_client
        self._api_base = api_base

    def _path(self, url: str) -> str:
        return url.removeprefix(self._api_base) or "/"

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        return cast(
            "httpx.Response",
            self._api_client.get(self._path(url), headers=headers, params=params),
        )

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        return cast(
            "httpx.Response",
            self._api_client.post(
                self._path(url),
                headers=headers,
                json=json,
                params=params,
            ),
        )

    def patch(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        return cast(
            "httpx.Response",
            self._api_client.patch(self._path(url), headers=headers, json=json),
        )

    def delete(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return cast(
            "httpx.Response",
            self._api_client.delete(self._path(url), headers=headers),
        )


def route_dashboard_httpx(
    api_client: TestClient,
    api_base: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BoundTestClientTransport(DashboardClientTransport):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(api_client, api_base)

    monkeypatch.setattr(httpx, "Client", BoundTestClientTransport)


def run_dashboard_panel(
    panel_name: str,
    api_client: TestClient,
    world: DashboardWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> AppTest:
    api_base = str(api_client.base_url).rstrip("/")
    route_dashboard_httpx(api_client, api_base, monkeypatch)
    path = ROOT / f"packages/beacon_ui/src/beacon_ui/dashboard/panels/{panel_name}.py"
    at = AppTest.from_file(str(path), default_timeout=30)
    at.session_state["api_key"] = world.alice_key
    at.session_state["api_base"] = api_base
    at.session_state["current_team_id"] = str(world.acme_team_id)
    at.session_state["current_project_id"] = str(world.chat_to_data_id)
    return at.run()
