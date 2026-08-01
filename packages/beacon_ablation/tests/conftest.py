"""Shared fixtures for beacon_ablation tests."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast

import pytest
from alembic import command
from alembic.config import Config
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.ids import uuid7
from pytest_postgresql import factories
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from uuid import UUID

    from beacon_runner.sut import SolutionUnderTest
    from beacon_runner.types import EvalItem, SolutionConfig
    from beacon_storage.models.attribution import Attribution
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

MIGRATIONS_DIR = "packages/beacon_storage/src/beacon_storage/migrations"
RECOVERY_ITEM_IDS = (
    "recovery-1",
    "recovery-5",
    "recovery-6",
    "recovery-7",
    "recovery-9",
    "recovery-10",
    "recovery-13",
    "recovery-17",
    "recovery-20",
    "recovery-25",
    "recovery-27",
    "recovery-28",
    "recovery-50",
    "recovery-53",
    "recovery-55",
    "recovery-59",
    "recovery-61",
    "recovery-64",
    "recovery-66",
    "recovery-70",
    "recovery-74",
    "recovery-78",
    "recovery-79",
    "recovery-82",
    "recovery-90",
    "recovery-12",
    "recovery-16",
    "recovery-32",
    "recovery-33",
    "recovery-2",
    "recovery-3",
    "recovery-18",
    "recovery-26",
    "recovery-34",
    "recovery-40",
    "recovery-46",
    "recovery-30",
    "recovery-14",
    "recovery-19",
    "recovery-22",
    "recovery-23",
    "recovery-41",
    "recovery-56",
    "recovery-68",
    "recovery-80",
    "recovery-105",
    "recovery-138",
    "recovery-154",
    "recovery-163",
    "recovery-176",
)

postgresql_proc = factories.postgresql_proc(port=None, unixsocketdir="/tmp")  # noqa: S108
postgresql = factories.postgresql("postgresql_proc")


class _PostgresInfo(Protocol):
    user: str
    host: str
    port: int
    dbname: str


class _PostgresConnection(Protocol):
    info: _PostgresInfo


@dataclass(frozen=True)
class SweepCall:
    run_id: UUID
    pass_idx: int
    parent_sweep_id: UUID


@dataclass(frozen=True)
class SweepResult:
    item_id: str
    attempt_idx: int
    outcome: str
    tokens_input: int
    tokens_output: int
    runtime_ms: int


@dataclass
class FakeSweepRunner:
    calls: list[SweepCall] = field(default_factory=list)
    arms: list[str] = field(default_factory=list)
    _results_by_run: dict[UUID, list[SweepResult]] = field(default_factory=dict)

    def run_single(
        self,
        *,
        sut: SolutionUnderTest,
        config: SolutionConfig,
        items: Sequence[EvalItem],
        suite: str,
        dataset_version: str,
        mode: str,
        pass_idx: int,
        parent_sweep_id: UUID,
        project_id: UUID,
        team_id: UUID,
        solution_id: UUID,
        sweep_arm: str,
    ) -> UUID:
        _ = (suite, dataset_version, mode, project_id, team_id, solution_id)
        self.arms.append(sweep_arm)
        run_id = uuid7()
        rows: list[SweepResult] = []
        for item in items:
            result = sut.invoke(item, config)
            passed = bool(result.output.get("passed"))
            rows.append(
                SweepResult(
                    item_id=item.item_id,
                    attempt_idx=pass_idx,
                    outcome="PASS" if passed else "FAIL",
                    tokens_input=result.tokens_input,
                    tokens_output=result.tokens_output,
                    runtime_ms=result.runtime_ms,
                )
            )
        self.calls.append(
            SweepCall(
                run_id=run_id,
                pass_idx=pass_idx,
                parent_sweep_id=parent_sweep_id,
            )
        )
        self._results_by_run[run_id] = rows
        return run_id

    def list_results_for_run(self, run_id: UUID) -> list[SweepResult]:
        return self._results_by_run[run_id]


@dataclass(frozen=True)
class SweepFixtures:
    sut: SolutionUnderTest
    base_config: SolutionConfig
    items: list[EvalItem]
    runner: FakeSweepRunner
    project_id: UUID
    team_id: UUID
    solution_id: UUID


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
    with engine.begin() as connection:
        connection.execute(text("DROP ROLE IF EXISTS beacon_app"))
        connection.execute(text("CREATE ROLE beacon_app"))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO beacon_app"))
        connection.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO beacon_app"
            )
        )
        connection.execute(text("GRANT EXECUTE ON FUNCTION current_user_id() TO beacon_app"))
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


@pytest.fixture
def sweep_fixtures(session: Session) -> SweepFixtures:
    from beacon_runner.dummy_sut import DummySUT
    from beacon_runner.types import EvalItem, SolutionConfig
    from beacon_storage.repository.projects import ProjectRepo
    from beacon_storage.repository.solutions import SolutionRepo
    from beacon_storage.repository.teams import TeamRepo
    from beacon_storage.repository.users import UserRepo

    user = UserRepo(session).create(email="ablate@example.com", name="A")
    team = TeamRepo(session).create(name="ablate-team")
    project = ProjectRepo(session).create(
        team_id=team.id,
        name="ablate-proj",
        created_by=user.id,
    )
    sut = DummySUT(owner_team_id=team.id)
    solution = SolutionRepo(session).create(
        team_id=team.id,
        solution_id="dummy",
        version=DummySUT.VERSION,
        owner_team=team.id,
        summary="Dummy",
        supported_modes=["EVAL", "NIGHTLY_LOO"],
        layers=[layer.model_dump(mode="json") for layer in sut.layers()],
        created_by=user.id,
    )
    items = [
        EvalItem(
            item_id=item_id,
            suite="dummy-suite",
            query={"question": f"q-{idx}"},
            ground_truth={"answer": "yes"},
            metadata={},
        )
        for idx, item_id in enumerate(RECOVERY_ITEM_IDS)
    ]
    session.commit()

    return SweepFixtures(
        sut=sut,
        base_config=SolutionConfig(
            model_id="dummy-m",
            prompt_version="recovery-v0",
            layers_enabled={"ontology": True, "retry_loop": True},
        ),
        items=items,
        runner=FakeSweepRunner(),
        project_id=project.id,
        team_id=team.id,
        solution_id=solution.id,
    )


@pytest.fixture
def recovery_sweep_result(
    session: Session,
    sweep_fixtures: SweepFixtures,
) -> list[Attribution]:
    from beacon_runner.harness import run_nightly_loo

    return run_nightly_loo(
        runner=sweep_fixtures.runner,
        session=session,
        sut=sweep_fixtures.sut,
        base_config=sweep_fixtures.base_config,
        items=sweep_fixtures.items,
        suite="dummy-suite",
        dataset_version="v1",
        K=5,
        project_id=sweep_fixtures.project_id,
        team_id=sweep_fixtures.team_id,
        solution_id=sweep_fixtures.solution_id,
    )
