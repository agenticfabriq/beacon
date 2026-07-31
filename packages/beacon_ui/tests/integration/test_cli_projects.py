from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest
from beacon_ui.cli.main import app as cli_app
from click.testing import CliRunner
from test_cli_teams import configure_cli_env  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

runner = CliRunner()


class _World(Protocol):
    alice_key: str
    acme_team_id: UUID
    chat_to_data_id: UUID
    carol_id: UUID


def test_projects_list(
    api_client: TestClient,
    world: _World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_cli_env(tmp_path, monkeypatch, api_client, world.alice_key)

    result = runner.invoke(
        cli_app,
        [
            "projects",
            "list",
            "--team",
            str(world.acme_team_id),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "chat-to-data-v3" in result.stdout


def test_projects_create(
    api_client: TestClient,
    world: _World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_cli_env(tmp_path, monkeypatch, api_client, world.alice_key)

    result = runner.invoke(
        cli_app,
        [
            "projects",
            "create",
            "--team",
            str(world.acme_team_id),
            "--name",
            "cli-created-project",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "cli-created-project" in result.stdout


def test_projects_members_add(
    api_client: TestClient,
    world: _World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_cli_env(tmp_path, monkeypatch, api_client, world.alice_key)

    result = runner.invoke(
        cli_app,
        [
            "projects",
            "members",
            "add",
            "--project",
            str(world.chat_to_data_id),
            "--user",
            str(world.carol_id),
            "--role",
            "project_viewer",
        ],
    )

    assert result.exit_code == 0, result.stdout
