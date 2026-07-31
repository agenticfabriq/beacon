"""E2E: PR_GATE catches a fake headline improvement."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from beacon_graders.composer import VerdictComposer
from beacon_graders.graders.dabstep_answer_matcher import DabstepAnswerMatcher
from beacon_runner.registry import SutRegistry
from beacon_runner.service.gate import GateService
from beacon_runner.types import (
    EvalItem,
    ExecutionResult,
    ExecutionStep,
    SolutionConfig,
    SolutionIdentity,
)
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.runs import HarnessMode, ResultStatus
from beacon_storage.models.runs import VerdictOutcome as StorageOutcome
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from pytest_postgresql import factories
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

pytestmark = [pytest.mark.integration, pytest.mark.e2e]

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
    yield engine
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = make_session_factory(engine)
    with factory() as session:
        yield session


@dataclass(frozen=True)
class _World:
    session: Session
    registry: SutRegistry
    project_id: UUID
    fake_improvement_sut_id: UUID
    curated_suite_id: UUID
    baseline_run_id: UUID


class _FakeImprovementSut:
    version = "0.1"

    def __init__(self, *, owner_team_id: UUID, outcomes: dict[str, Sequence[bool]]) -> None:
        self._owner_team_id = owner_team_id
        self._outcomes = outcomes

    def identity(self) -> SolutionIdentity:
        return SolutionIdentity(
            solution_id="fake-improvement",
            version=self.version,
            owner_team=self._owner_team_id,
            summary="fake headline improvement SUT",
            supported_modes=["PR_GATE"],
            layers=[],
        )

    def layers(self) -> list[object]:
        return []

    def validate_config(self, config: SolutionConfig) -> list[str]:  # noqa: ARG002
        return []

    def invoke(self, item: EvalItem, config: SolutionConfig) -> ExecutionResult:
        pass_idx = int(config.extras["gate_pass_idx"])
        passed = self._outcomes[item.item_id][pass_idx]
        return ExecutionResult(
            output={"answer": "yes" if passed else "no"},
            output_kind="answer",
            trace=ExecutionStep(
                uuid=f"root-{item.item_id}-{pass_idx}",
                name="fake_improvement",
                level="workflow",
            ),
        )


def _make_world(session: Session) -> _World:
    user = UserRepo(session).create(email="fake-gate@example.com", name="Fake Gate")
    team = TeamRepo(session).create(name="fake-gate-team")
    project = ProjectRepo(session).create(team_id=team.id, name="fake-gate", created_by=user.id)
    baseline_solution = SolutionRepo(session).create(
        team_id=team.id,
        solution_id="baseline",
        version="0.1",
        owner_team=team.id,
        summary="",
        supported_modes=["PR_GATE"],
        layers=[],
        created_by=user.id,
    )
    fake_solution = SolutionRepo(session).create(
        team_id=team.id,
        solution_id="fake-improvement",
        version="0.1",
        owner_team=team.id,
        summary="",
        supported_modes=["PR_GATE"],
        layers=[],
        created_by=user.id,
    )
    suite = SuiteRepo(session).create(
        project_id=project.id,
        team_id=team.id,
        name="fake-curated-50",
        description="curated subset",
        method="separability_gain",
        suite_metadata={"subset_tag": "curated_50_fake"},
        created_by=user.id,
    )

    baseline = [(True, True, True)] * 6 + [(False, False, False)] * 4
    fake = [(True, True, False)] * 6 + [(True, False, False)] + [(False, False, False)] * 3
    item_ids = _create_items(session, team_id=team.id, user_id=user.id, suite_name=suite.name)
    SuiteRepo(session).add_items(suite_id=suite.id, item_ids=item_ids)
    baseline_run_id = _seed_baseline_runs(
        session,
        team_id=team.id,
        project_id=project.id,
        user_id=user.id,
        solution_id=baseline_solution.id,
        suite=suite.name,
        item_ids=item_ids,
        attempts=baseline,
    )
    project.baseline_run_id = baseline_run_id

    registry = SutRegistry()
    registry.register(
        _FakeImprovementSut(
            owner_team_id=team.id,
            outcomes={
                str(item_id): tuple(attempts)
                for item_id, attempts in zip(item_ids, fake, strict=True)
            },
        )
    )
    session.commit()
    return _World(
        session=session,
        registry=registry,
        project_id=project.id,
        fake_improvement_sut_id=fake_solution.id,
        curated_suite_id=suite.id,
        baseline_run_id=baseline_run_id,
    )


def _create_items(session: Session, *, team_id: UUID, user_id: UUID, suite_name: str) -> list[UUID]:
    item_ids: list[UUID] = []
    for idx in range(10):
        item = EvalItemRepo(session).insert_new_version(
            item_id=uuid4(),
            valid_from=datetime.now(UTC) + timedelta(microseconds=idx),
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite=suite_name,
            team_id=team_id,
            solution_id=None,
            dataset_version="v0",
            item_input={"question": f"question {idx}"},
            gold_answer={"answer": "yes"},
            item_metadata={},
            created_by=user_id,
        )
        item_ids.append(item.item_id)
    return item_ids


def _seed_baseline_runs(
    session: Session,
    *,
    team_id: UUID,
    project_id: UUID,
    user_id: UUID,
    solution_id: UUID,
    suite: str,
    item_ids: Sequence[UUID],
    attempts: Sequence[Sequence[bool]],
) -> UUID:
    group_id = uuid4()
    baseline_run_id: UUID | None = None
    for pass_idx in range(3):
        run = RunRepo(session).create(
            team_id=team_id,
            project_id=project_id,
            solution_id=solution_id,
            suite=suite,
            dataset_version="v0",
            mode=HarnessMode.PR_GATE,
            pass_idx=pass_idx,
            parent_sweep_id=group_id,
            config={},
            created_by=user_id,
        )
        if pass_idx == 0:
            baseline_run_id = run.id
        RunRepo(session).mark_completed(run.id)
        for item_id, item_attempts in zip(item_ids, attempts, strict=True):
            passed = item_attempts[pass_idx]
            ResultRepo(session).create(
                team_id=team_id,
                project_id=project_id,
                run_id=run.id,
                item_id=str(item_id),
                attempt_idx=pass_idx,
                output={"answer": "yes" if passed else "no"},
                output_kind="answer",
                tokens_input=0,
                tokens_output=0,
                runtime_ms=0,
                status=ResultStatus.COMPLETED,
                outcome=StorageOutcome.PASS if passed else StorageOutcome.FAIL,
                error=None,
            )
    assert baseline_run_id is not None
    return baseline_run_id


def test_pr_gate_catches_fake_improvement(session: Session) -> None:
    world = _make_world(session)
    svc = GateService(
        world.session,
        registry=world.registry,
        composer=VerdictComposer(graders=[DabstepAnswerMatcher()]),
    )

    res = svc.run_gate(
        project_id=world.project_id,
        sut_id=world.fake_improvement_sut_id,
        suite_id=world.curated_suite_id,
        commit_sha="fake-abc",
        baseline_run_id=world.baseline_run_id,
    )

    assert res.outcome == "FAIL"
    assert res.delta_pass_at_3 > 0
    assert res.delta_pass_hat_3 < -0.05
    assert "pass^3" in res.reason
