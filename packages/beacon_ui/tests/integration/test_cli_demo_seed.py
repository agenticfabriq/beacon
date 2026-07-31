from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.runs import HarnessMode, RunStatus
from beacon_storage.models.tenancy import ApiKey
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_ui.cli.demo import CURATED_SUITE, DEMO_SUITE
from beacon_ui.cli.main import app as cli_app
from click.testing import CliRunner
from sqlalchemy import func, select

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_storage.models.tenancy import Project
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

runner = CliRunner()


def _project_by_name(session: Session, team_id: UUID, name: str) -> Project:
    for project in ProjectRepo(session).list_for_team(team_id, include_archived=True):
        if project.name == name:
            return project
    raise AssertionError(f"project {name!r} not found")


def _demo_items(session: Session) -> list[object]:
    return [
        item
        for item in EvalItemRepo(session).list_active(
            suite=DEMO_SUITE,
            tier=EvalItemTier.HUMAN_VERIFIED,
            team_id=None,
        )
        if item.question_hash and item.question_hash.startswith(f"demo:{DEMO_SUITE}:")
    ]


def _demo_api_key_count(session: Session) -> int:
    count = session.scalar(
        select(func.count())
        .select_from(ApiKey)
        .where(
            ApiKey.label.in_(["demo-alice", "demo-carol"]),
            ApiKey.revoked_at.is_(None),
        )
    )
    assert count is not None
    return int(count)


def test_demo_seed_creates_idempotent_fixtures(db_url: str, session: Session) -> None:
    first = runner.invoke(
        cli_app,
        ["demo", "seed", "--database-url", db_url, "--api-base", "http://testserver"],
    )

    assert first.exit_code == 0, first.stdout
    assert "Demo seed complete." in first.stdout
    assert "alice@example.com: bcn_demo_" in first.stdout
    session.expire_all()

    acme = TeamRepo(session).get_by_name("acme")
    globex = TeamRepo(session).get_by_name("globex")
    assert acme is not None
    assert globex is not None
    acme_project = _project_by_name(session, acme.id, "chat-to-data-v3.2")
    _project_by_name(session, globex.id, "globex-sql-v1.4")

    items = _demo_items(session)
    assert len(items) == 50
    curated = SuiteRepo(session).get_by_project_and_name(acme_project.id, CURATED_SUITE)
    assert curated is not None
    assert len(SuiteRepo(session).list_item_ids(curated.id)) == 50

    runs = RunRepo(session).list_for_project(
        acme_project.id,
        suite=CURATED_SUITE,
        mode=HarnessMode.NIGHTLY_LOO,
        status=RunStatus.COMPLETED,
    )
    assert len(runs) == 3
    assert {run.pass_idx for run in runs} == {0, 1, 2}
    baseline = next(run for run in runs if run.pass_idx == 0)
    assert acme_project.baseline_run_id == baseline.id
    assert len(ResultRepo(session).list_for_run(baseline.id)) == 50
    assert _demo_api_key_count(session) == 2

    second = runner.invoke(
        cli_app,
        ["demo", "seed", "--database-url", db_url, "--api-base", "http://testserver"],
    )

    assert second.exit_code == 0, second.stdout
    assert "existing key not reprinted" in second.stdout
    session.expire_all()
    assert len(_demo_items(session)) == 50
    assert _demo_api_key_count(session) == 2
    rerun_runs = RunRepo(session).list_for_project(
        acme_project.id,
        suite=CURATED_SUITE,
        mode=HarnessMode.NIGHTLY_LOO,
        status=RunStatus.COMPLETED,
    )
    assert len(rerun_runs) == 3
