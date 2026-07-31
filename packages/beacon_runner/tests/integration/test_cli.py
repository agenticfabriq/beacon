from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from beacon_runner.cli import main
from beacon_runner.dummy_sut import DummySUT
from beacon_storage.models.runs import RunStatus
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from click.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

    from beacon_storage.models import Project, Team, User
    from pytest import MonkeyPatch
    from sqlalchemy.orm import Session

    _Ctx = tuple[User, Team, Project]

pytestmark = pytest.mark.integration


@pytest.fixture
def _ctx(session: Session) -> _Ctx:
    user = UserRepo(session).create(email="cli@example.com", name="CLI")
    team = TeamRepo(session).create(name="cli-team")
    project = ProjectRepo(session).create(team_id=team.id, name="cli-proj", created_by=user.id)
    MembershipRepo(session).grant(
        user_id=user.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=team.id,
        role=Role.TEAM_ADMIN,
    )
    MembershipRepo(session).grant(
        user_id=user.id,
        scope_kind=ScopeKind.PROJECT,
        scope_id=project.id,
        role=Role.PROJECT_OWNER,
    )
    session.commit()
    return user, team, project


def test_suts_register_writes_row(
    db_url: str, _ctx: _Ctx, monkeypatch: MonkeyPatch, session: Session
) -> None:
    user, team, _project = _ctx
    monkeypatch.setenv("DATABASE_URL", db_url)

    result = CliRunner().invoke(
        main,
        [
            "suts",
            "register",
            "--team",
            team.name,
            "--as",
            user.email,
            "--solution-id",
            "dummy",
            "--version",
            DummySUT.VERSION,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "registered" in result.output.lower()
    solution = SolutionRepo(session).get_by_team_and_solution(team.id, "dummy", DummySUT.VERSION)
    assert solution is not None
    assert solution.team_id == team.id


def test_eval_run_persists_results(
    db_url: str,
    _ctx: _Ctx,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    session: Session,
) -> None:
    user, team, project = _ctx
    SolutionRepo(session).create(
        team_id=team.id,
        solution_id="dummy",
        version=DummySUT.VERSION,
        owner_team=team.id,
        summary="",
        supported_modes=["EVAL"],
        layers=[],
        created_by=user.id,
    )
    session.commit()
    items = [
        {
            "item_id": f"i-{idx}",
            "suite": "dummy_smoke_v1",
            "query": {"question": "?"},
            "ground_truth": {"answer": "yes"},
            "metadata": {},
        }
        for idx in range(5)
    ]
    items_path = tmp_path / "items.jsonl"
    items_path.write_text("\n".join(json.dumps(item) for item in items), encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", db_url)

    result = CliRunner().invoke(
        main,
        [
            "eval",
            "run",
            "--project-id",
            str(project.id),
            "--as",
            user.email,
            "--solution-id",
            "dummy",
            "--version",
            DummySUT.VERSION,
            "--suite",
            "dummy_smoke_v1",
            "--items",
            str(items_path),
            "--dataset-version",
            "v0",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.startswith("run_id=")
    runs = RunRepo(session).list_for_project(project.id)
    assert len(runs) == 1
    assert result.output.strip() == f"run_id={runs[0].id}"
    assert runs[0].status == RunStatus.COMPLETED
