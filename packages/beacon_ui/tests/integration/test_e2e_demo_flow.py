from __future__ import annotations

import json
from textwrap import dedent
from typing import TYPE_CHECKING, Protocol

import pytest
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_ui.cli.http import CliHttpError
from beacon_ui.cli.main import app as cli_app
from click.testing import CliRunner, Result
from dashboard_panel_test import run_dashboard_panel  # type: ignore[import-not-found]
from test_cli_teams import route_cli_httpx  # type: ignore[import-not-found]

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
    carol_key: str
    acme_team_id: UUID
    globex_team_id: UUID
    chat_to_data_id: UUID
    globex_research_id: UUID


def _json_stdout(result: Result) -> dict[str, object]:
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert isinstance(body, dict)
    return body


def _seed_review_item(session: Session, world: _World) -> str:
    item = EvalItemRepo(session).create(
        tier=EvalItemTier.EXECUTION_CONFIRMED,
        suite="demo_flow_review",
        team_id=world.acme_team_id,
        dataset_version="demo-v1",
        item_input={"question": "How many demo rows are visible?"},
        gold_answer={"sql": "SELECT 1"},
        item_metadata={"source": "task-31-demo-flow"},
        created_by=world.alice_id,
    )
    session.commit()
    return str(item.item_id)


def _write_sut_file(path: Path) -> None:
    path.write_text(
        dedent(
            """
            from uuid import UUID

            from beacon_runner.types import Layer, SolutionIdentity


            class SUT:
                def identity(self):
                    return SolutionIdentity(
                        solution_id="demo-flow-sut",
                        version="0.1",
                        owner_team=UUID("00000000-0000-0000-0000-000000000000"),
                        summary="Demo flow SUT",
                        supported_modes=["EVAL"],
                    )

                def layers(self):
                    return [
                        Layer(
                            name="router",
                            description="demo router",
                            ablation_semantic="disabled",
                            instrumentation="native",
                        )
                    ]
            """
        ),
        encoding="utf-8",
    )


def test_e2e_demo_flow(
    api_client: TestClient,
    world: _World,
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_base = str(api_client.base_url).rstrip("/")
    monkeypatch.setenv("BEACON_CTX_FILE", str(tmp_path / "ctx.json"))
    monkeypatch.delenv("BEACON_API_BASE", raising=False)
    monkeypatch.delenv("BEACON_API_KEY", raising=False)
    monkeypatch.delenv("BEACON_TEAM", raising=False)
    monkeypatch.delenv("BEACON_PROJECT", raising=False)
    route_cli_httpx(api_client, api_base, monkeypatch)

    login = runner.invoke(
        cli_app,
        ["login", "--api-base", api_base, "--api-key", world.alice_key],
    )
    assert login.exit_code == 0, login.stdout
    assert "alice@example.com" in login.stdout

    ctx_set = runner.invoke(
        cli_app,
        [
            "ctx",
            "set",
            "--team",
            str(world.acme_team_id),
            "--project",
            str(world.chat_to_data_id),
        ],
    )
    assert ctx_set.exit_code == 0, ctx_set.stdout

    ctx = _json_stdout(runner.invoke(cli_app, ["ctx", "show", "--format", "json"]))
    assert ctx["api_base"] == api_base
    assert ctx["api_key"] == "***"
    assert ctx["team_id"] == str(world.acme_team_id)
    assert ctx["project_id"] == str(world.chat_to_data_id)

    sut_file = tmp_path / "demo_sut.py"
    _write_sut_file(sut_file)
    sut = _json_stdout(
        runner.invoke(
            cli_app,
            ["suts", "register", str(sut_file), "--team", str(world.acme_team_id)],
        )
    )
    sut_id = str(sut["id"])
    assert sut["solution_id"] == "demo-flow-sut"

    attached = _json_stdout(
        runner.invoke(
            cli_app,
            [
                "projects",
                "solutions",
                "add",
                "--project",
                str(world.chat_to_data_id),
                "--sut",
                sut_id,
            ],
        )
    )
    assert attached["solution_id"] == sut_id

    item_id = _seed_review_item(session, world)
    suite_response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/suites",
        headers={"X-API-Key": world.alice_key},
        json={
            "name": "demo-flow-suite",
            "kind": "manual",
            "item_ids": [item_id],
            "metadata": {"dataset_version": "demo-v1"},
        },
    )
    assert suite_response.status_code == 201, suite_response.text
    suite = suite_response.json()
    assert suite["item_count"] == 1

    run = _json_stdout(
        runner.invoke(
            cli_app,
            [
                "eval",
                "run",
                "--project",
                str(world.chat_to_data_id),
                "--sut",
                sut_id,
                "--suite",
                str(suite["suite_id"]),
                "--mode",
                "EVAL",
            ],
        )
    )
    run_id = str(run["run_id"])
    assert run["status"] == "queued"

    runs_response = api_client.get(
        f"/v1/projects/{world.chat_to_data_id}/runs",
        headers={"X-API-Key": world.alice_key},
    )
    assert runs_response.status_code == 200, runs_response.text
    assert run_id in {row["run_id"] for row in runs_response.json()}

    decision_response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/review-queue/{item_id}/decide",
        headers={"X-API-Key": world.alice_key},
        json={"action": "accept", "reason": "demo answer matches gold"},
    )
    assert decision_response.status_code == 200, decision_response.text
    decision = decision_response.json()
    assert decision["action"] == "accept"
    assert decision["new_tier"] == "human_verified"

    overview = run_dashboard_panel("overview", api_client, world, monkeypatch)
    assert not overview.exception
    review_queue = run_dashboard_panel("review_queue", api_client, world, monkeypatch)
    assert not review_queue.exception

    carol_login = runner.invoke(
        cli_app,
        ["login", "--api-base", api_base, "--api-key", world.carol_key],
    )
    assert carol_login.exit_code == 0, carol_login.stdout
    assert "carol@example.com" in carol_login.stdout
    carol_ctx = runner.invoke(
        cli_app,
        [
            "ctx",
            "set",
            "--team",
            str(world.globex_team_id),
            "--project",
            str(world.globex_research_id),
        ],
    )
    assert carol_ctx.exit_code == 0, carol_ctx.stdout

    denied = runner.invoke(
        cli_app,
        ["projects", "list", "--team", str(world.acme_team_id), "--format", "json"],
    )
    assert denied.exit_code != 0
    assert isinstance(denied.exception, CliHttpError)
    assert denied.exception.status == 403
