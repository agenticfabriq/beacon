from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest
from beacon_storage.repository.project_solutions import ProjectSolutionRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_ui.cli.main import app as cli_app
from click.testing import CliRunner
from test_cli_eval import _create_suite  # type: ignore[import-not-found]
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
    chat_to_data_id: UUID


def _create_layered_sut(session: Session, world: _World) -> UUID:
    sut = SolutionRepo(session).create(
        team_id=world.acme_team_id,
        solution_id="lay-cli",
        version="0.1",
        owner_team=world.acme_team_id,
        summary="Layered CLI SUT",
        supported_modes=["NIGHTLY_LOO"],
        layers=[
            {
                "name": "L1",
                "description": "layer one",
                "ablation_semantic": "disabled",
                "instrumentation": "native",
            }
        ],
        created_by=world.alice_id,
    )
    ProjectSolutionRepo(session).link(
        team_id=world.acme_team_id,
        project_id=world.chat_to_data_id,
        solution_id=sut.id,
    )
    session.commit()
    return sut.id


def test_attribution_sweep_invokes_endpoint(
    api_client: TestClient,
    world: _World,
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sut_id = _create_layered_sut(session, world)
    suite_id = _create_suite(api_client, world, "cli-attr-suite")
    configure_cli_env(tmp_path, monkeypatch, api_client, world.alice_key)

    result = runner.invoke(
        cli_app,
        [
            "attribution",
            "sweep",
            "--project",
            str(world.chat_to_data_id),
            "--sut",
            str(sut_id),
            "--suite",
            suite_id,
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "run_id" in result.stdout


def test_attribution_show_renders(
    api_client: TestClient,
    world: _World,
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sut_id = _create_layered_sut(session, world)
    suite_id = _create_suite(api_client, world, "cli-attr-show-suite")
    configure_cli_env(tmp_path, monkeypatch, api_client, world.alice_key)

    result = runner.invoke(
        cli_app,
        [
            "attribution",
            "show",
            "--project",
            str(world.chat_to_data_id),
            "--sut",
            str(sut_id),
            "--suite",
            suite_id,
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert result.stdout.strip() == "[]"
