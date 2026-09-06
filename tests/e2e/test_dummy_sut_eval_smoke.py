"""E2E smoke: DummySUT registered -> EVAL run -> composer -> Postgres rows."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol, cast

import pytest
from alembic import command
from alembic.config import Config
from beacon_graders.composer import VerdictComposer
from beacon_graders.graders.dabstep_answer_matcher import DabstepAnswerMatcher
from beacon_runner.dummy_sut import DummySUT
from beacon_runner.harness import HarnessRunner
from beacon_runner.registry import SutRegistry
from beacon_runner.types import EvalItem, SolutionConfig
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.models.runs import HarnessMode, ResultStatus, RunStatus, VerdictOutcome
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.traces import TraceRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.repository.verdicts import VerdictRepo
from beacon_storage.rls import set_current_user
from pytest_postgresql import factories
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from uuid import UUID

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

MIGRATIONS_DIR = "packages/beacon_storage/src/beacon_storage/migrations"

postgresql_proc = factories.postgresql_proc(port=None, unixsocketdir="/tmp")  # noqa: S108
postgresql = factories.postgresql("postgresql_proc")


class _PostgresInfo(Protocol):
    user: str
    host: str
    port: int
    dbname: str


class _PostgresConnection(Protocol):
    info: _PostgresInfo


@pytest.fixture
def db_url(request: pytest.FixtureRequest) -> str:
    if url := os.environ.get("DATABASE_URL"):
        return url
    postgresql = cast("_PostgresConnection", request.getfixturevalue("postgresql"))
    info = postgresql.info
    return f"postgresql+psycopg://{info.user}@{info.host}:{info.port}/{info.dbname}"


@pytest.fixture
def engine(db_url: str) -> Iterator[Engine]:
    os.environ["DATABASE_URL"] = db_url
    engine = make_engine(db_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    config = Config(f"{MIGRATIONS_DIR}/alembic.ini")
    config.set_main_option("script_location", MIGRATIONS_DIR)
    command.upgrade(config, "head")
    # The serving role is provisioned by migration 0025, not here. Dropping it
    # is no longer possible once it holds grants, and a parallel provisioning
    # path is what let the role exist without LOGIN while 0025's guard skipped
    # its own CREATE.
    yield engine
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()


def _use_app_role(session: Session) -> None:
    session.execute(text("SET LOCAL ROLE beacon_app"))


@pytest.mark.integration
@pytest.mark.e2e
def test_dummy_sut_eval_smoke(engine: Engine) -> None:
    factory = make_session_factory(engine)

    with factory() as session:
        admin = UserRepo(session).create(email="admin@e2e.com", name="Admin")
        runner_user = UserRepo(session).create(email="runner@e2e.com", name="Runner")
        team = TeamRepo(session).create(name="e2e-team")
        MembershipRepo(session).grant(
            user_id=admin.id,
            scope_kind=ScopeKind.GLOBAL,
            scope_id=admin.id,
            role=Role.BEACON_ADMIN,
        )
        MembershipRepo(session).grant(
            user_id=runner_user.id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team.id,
            role=Role.TEAM_MEMBER,
        )
        suite_record = SuiteRepo(session).create(
            team_id=team.id,
            name="dummy_smoke_v1",
            description="",
            method="manual",
            suite_metadata={},
            created_by=runner_user.id,
        )
        session.commit()
        team_id = team.id
        runner_id = runner_user.id
        suite_id = suite_record.id

    with factory() as session:
        set_current_user(session, runner_id)
        sut = DummySUT(owner_team_id=team_id)
        solution = SolutionRepo(session).create(
            team_id=team_id,
            solution_id="dummy",
            version=DummySUT.VERSION,
            owner_team=team_id,
            summary="",
            supported_modes=list(sut.identity().supported_modes),
            layers=[layer.model_dump(mode="json") for layer in sut.layers()],
            created_by=runner_id,
        )
        session.commit()
        solution_record_id = solution.id

    registry = SutRegistry()
    registry.register(DummySUT(owner_team_id=team_id))
    runner = HarnessRunner(
        session_factory=factory,
        registry=registry,
        composer=VerdictComposer(graders=[DabstepAnswerMatcher()]),
        max_workers=4,
    )
    items = [
        EvalItem(
            item_id=f"i-{idx}",
            suite="dummy_smoke_v1",
            query={"question": "what is yes?"},
            ground_truth={"answer": "yes"},
            metadata={},
        )
        for idx in range(50)
    ]
    run_id: UUID = runner.run_single(
        team_id=team_id,
        suite_id=suite_id,
        user_id=runner_id,
        solution_record_id=solution_record_id,
        items=items,
        config=SolutionConfig(model_id="dummy", prompt_version="v0", layers_enabled={}),
        suite="dummy_smoke_v1",
        dataset_version="v0",
        pass_idx=0,
    )

    with factory() as session:
        run = RunRepo(session).get(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED
        assert run.mode == HarnessMode.EVAL

        results = ResultRepo(session).list_for_run(run_id)
        assert len(results) == 50
        assert {result.status for result in results} == {ResultStatus.COMPLETED}

        outcomes = [result.outcome for result in results]
        passes = sum(1 for outcome in outcomes if outcome == VerdictOutcome.PASS)
        assert 25 <= passes <= 45, f"expected 25..45 passes, got {passes}"

        for result in results:
            verdicts = VerdictRepo(session).list_for_result(result.id)
            assert len(verdicts) == 1
            assert verdicts[0].grader == "dabstep_answer_matcher"

        for result in results:
            trace = TraceRepo(session).get_for_result(result.id)
            assert trace is not None
            child_levels = {child["level"] for child in trace.step_tree["children"]}
            assert "layer:ontology" in child_levels
            assert "layer:retry_loop" in child_levels

    with factory() as session:
        outsider = UserRepo(session).create(email="outsider@e2e.com", name="O")
        session.commit()
        outsider_id = outsider.id

    with factory() as session:
        _use_app_role(session)
        set_current_user(session, outsider_id)
        assert ResultRepo(session).list_for_run(run_id) == []
        assert RunRepo(session).list_for_suite(suite_id) == []
