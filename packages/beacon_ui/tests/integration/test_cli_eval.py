from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest
from beacon_storage.repository.solutions import SolutionRepo
from beacon_ui.cli.main import app as cli_app
from click.testing import CliRunner
from test_cli_teams import configure_cli_env  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

runner = CliRunner()


class _World(Protocol):
    alice_id: UUID
    alice_key: str
    acme_team_id: UUID


def _create_suite(api_client: TestClient, world: _World, name: str) -> str:
    response = api_client.post(
        f"/v1/teams/{world.acme_team_id}/suites",
        headers={"X-API-Key": world.alice_key},
        json={"name": name, "kind": "manual", "item_ids": []},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["suite_id"])


def test_eval_run_invokes_endpoint(
    api_client: TestClient,
    world: _World,
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sut = SolutionRepo(session).create(
        team_id=world.acme_team_id,
        solution_id="dummy-cli",
        version="0.1",
        owner_team=world.acme_team_id,
        summary="Dummy CLI SUT",
        supported_modes=["EVAL"],
        layers=[],
        created_by=world.alice_id,
    )
    session.commit()
    suite_id = _create_suite(api_client, world, "cli-suite")
    configure_cli_env(tmp_path, monkeypatch, api_client, world.alice_key)

    result = runner.invoke(
        cli_app,
        [
            "eval",
            "run",
            "--sut",
            str(sut.id),
            "--suite",
            suite_id,
            "--mode",
            "EVAL",
            "--k",
            "2",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "run_id" in result.stdout
