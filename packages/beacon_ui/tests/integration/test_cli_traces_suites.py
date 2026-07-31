"""beacon traces import, beacon suites create / select CLI."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_registry.items import ItemService
from beacon_registry.suites import SuiteService
from beacon_registry.types import EvalItemTier
from beacon_storage.models.production_traces import ProductionTrace
from click.testing import CliRunner
from sqlalchemy import select

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from beacon_storage.models.tenancy import Project, Team
    from pytest import MonkeyPatch
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _alice_id(session: Session) -> UUID:
    user = UserService(session).upsert_from_oidc(
        OidcClaims(subject="alice", email="alice@example.com", name="Alice")
    )
    return user.id


def test_traces_import_jsonl_file(
    monkeypatch: MonkeyPatch,
    db_url: str,
    session: Session,
    alice_team_membership: Team,
    alice_project: Project,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    lines = [
        json.dumps(
            {
                "item_input": {"q": f"q-{i}"},
                "item_output": {"a": f"a-{i}"},
                "trace": {"name": "root", "level": "workflow", "children": []},
                "metadata": {"latency_ms": 10 * i},
            }
        )
        for i in range(3)
    ]
    input_path = tmp_path / "prod.jsonl"
    input_path.write_text("\n".join(lines), encoding="utf-8")

    from beacon_runner.cli import main

    result = CliRunner().invoke(
        main,
        [
            "traces",
            "import",
            "--solution-id",
            "acme-chat-to-data",
            "--project",
            str(alice_project.id),
            "--format",
            "jsonl",
            "--input",
            str(input_path),
            "--as",
            "alice@example.com",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "3" in result.output

    rows = list(
        session.scalars(
            select(ProductionTrace).where(
                ProductionTrace.team_id == alice_team_membership.id,
                ProductionTrace.project_id == alice_project.id,
                ProductionTrace.solution_id == "acme-chat-to-data",
            )
        )
    )
    assert len(rows) == 3


def test_suites_create(
    monkeypatch: MonkeyPatch,
    db_url: str,
    session: Session,
    alice_project: Project,
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)

    from beacon_runner.cli import main

    result = CliRunner().invoke(
        main,
        [
            "suites",
            "create",
            "--project",
            str(alice_project.id),
            "--name",
            "manual-set",
            "--method",
            "manual",
            "--as",
            "alice@example.com",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "manual-set" in result.output

    suite = SuiteService(session).get_by_project_and_name(alice_project.id, "manual-set")
    assert suite is not None
    assert suite.method == "manual"


def test_suites_select_separability(
    monkeypatch: MonkeyPatch,
    db_url: str,
    session: Session,
    alice_team_membership: Team,
    alice_project: Project,
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    user_id = _alice_id(session)

    item_service = ItemService(session)
    item_ids = [
        item_service.create_item(
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite="bird",
            team_id=alice_team_membership.id,
            solution_id=None,
            dataset_version="v",
            item_input={"i": i},
            gold_answer={},
            item_metadata={},
            created_by=user_id,
        )
        for i in range(4)
    ]

    from beacon_storage.models.runs import HarnessMode, ResultStatus, VerdictOutcome
    from beacon_storage.repository.results import ResultRepo
    from beacon_storage.repository.runs import RunRepo
    from beacon_storage.repository.solutions import SolutionRepo

    solution = SolutionRepo(session).create(
        team_id=alice_team_membership.id,
        solution_id="dummy",
        version="0.2",
        owner_team=alice_team_membership.id,
        summary="",
        supported_modes=["EVAL"],
        layers=[],
        created_by=user_id,
    )
    for config_idx in range(3):
        run = RunRepo(session).create(
            team_id=alice_team_membership.id,
            project_id=alice_project.id,
            solution_id=solution.id,
            suite="bird",
            dataset_version="v",
            mode=HarnessMode.EVAL,
            pass_idx=config_idx,
            config={},
            created_by=user_id,
        )
        for position, item_id in enumerate(item_ids):
            passed = (position + config_idx) % 2 == 0
            ResultRepo(session).create(
                team_id=alice_team_membership.id,
                project_id=alice_project.id,
                run_id=run.id,
                item_id=str(item_id),
                attempt_idx=0,
                output={},
                output_kind="answer",
                tokens_input=0,
                tokens_output=0,
                runtime_ms=0,
                status=ResultStatus.COMPLETED,
                outcome=VerdictOutcome.PASS if passed else VerdictOutcome.FAIL,
                error=None,
            )
    session.commit()

    from beacon_runner.cli import main

    runner = CliRunner()
    create_result = runner.invoke(
        main,
        [
            "suites",
            "create",
            "--project",
            str(alice_project.id),
            "--name",
            "curated-2",
            "--method",
            "separability_gain",
            "--as",
            "alice@example.com",
        ],
    )
    assert create_result.exit_code == 0, create_result.output

    result = runner.invoke(
        main,
        [
            "suites",
            "select",
            "--project",
            str(alice_project.id),
            "--suite",
            "curated-2",
            "--source-suite",
            "bird",
            "--method",
            "separability",
            "--n",
            "2",
            "--k",
            "3",
            "--as",
            "alice@example.com",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "2 item" in result.output.lower() or "chose 2" in result.output.lower()

    target = SuiteService(session).get_by_project_and_name(alice_project.id, "curated-2")
    assert target is not None
    assert len(SuiteService(session).list_item_ids(target.id)) == 2
