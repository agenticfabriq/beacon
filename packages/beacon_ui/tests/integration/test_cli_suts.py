from __future__ import annotations

from textwrap import dedent
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


def test_suts_list_runs(
    api_client: TestClient,
    world: _World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_cli_env(tmp_path, monkeypatch, api_client, world.alice_key)

    result = runner.invoke(
        cli_app,
        [
            "suts",
            "list",
            "--team",
            str(world.acme_team_id),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "chat-to-data-v3" in result.stdout


def test_suts_register_posts_identity(
    api_client: TestClient,
    world: _World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_cli_env(tmp_path, monkeypatch, api_client, world.alice_key)
    sut_file = tmp_path / "cli_sut.py"
    sut_file.write_text(
        dedent(
            """
            from uuid import UUID

            from beacon_runner.types import Layer, SolutionIdentity


            class SUT:
                def identity(self):
                    return SolutionIdentity(
                        solution_id="cli-registered",
                        version="0.1",
                        owner_team=UUID("00000000-0000-0000-0000-000000000000"),
                        summary="CLI registered SUT",
                        supported_modes=["EVAL"],
                    )

                def layers(self):
                    return [
                        Layer(
                            name="router",
                            description="request router",
                            ablation_semantic="disabled",
                            instrumentation="native",
                        )
                    ]
            """
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        cli_app,
        [
            "suts",
            "register",
            str(sut_file),
            "--team",
            str(world.acme_team_id),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "cli-registered" in result.stdout
