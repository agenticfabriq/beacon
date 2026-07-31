from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx
import pytest
from beacon_ui.cli.main import app as cli_app
from click.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

runner = CliRunner()


class _World(Protocol):
    alice_key: str
    acme_team_id: UUID


class _CliTestTransport:
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


def route_cli_httpx(
    api_client: TestClient,
    api_base: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BoundTestClientTransport(_CliTestTransport):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(api_client, api_base)

    monkeypatch.setattr(httpx, "Client", BoundTestClientTransport)


def configure_cli_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_client: TestClient,
    api_key: str,
) -> None:
    api_base = str(api_client.base_url).rstrip("/")
    monkeypatch.setenv("BEACON_CTX_FILE", str(tmp_path / "c.json"))
    monkeypatch.setenv("BEACON_API_BASE", api_base)
    monkeypatch.setenv("BEACON_API_KEY", api_key)
    route_cli_httpx(api_client, api_base, monkeypatch)


def test_teams_list(
    api_client: TestClient,
    world: _World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_cli_env(tmp_path, monkeypatch, api_client, world.alice_key)

    result = runner.invoke(cli_app, ["teams", "list", "--format", "json"])

    assert result.exit_code == 0, result.stdout
    assert "acme" in result.stdout
