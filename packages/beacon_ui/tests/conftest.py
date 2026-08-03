"""Shared API integration fixtures."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

import pytest
from alembic import command
from alembic.config import Config
from beacon_iam.auth.api_key import generate_api_key, hash_api_key
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.repository.api_keys import ApiKeyRepo
from fastapi.testclient import TestClient
from pytest_postgresql import factories
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from uuid import UUID

    from beacon_storage.models.suites import Suite
    from beacon_storage.models.tenancy import Team
    from pytest import MonkeyPatch
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


@dataclass(frozen=True)
class SeededWorld:
    alice_id: UUID
    bob_id: UUID
    carol_id: UUID
    acme_team_id: UUID
    globex_team_id: UUID
    acme_suite_id: UUID
    globex_suite_id: UUID
    acme_solution_id: UUID
    globex_solution_id: UUID
    acme_run_id: UUID
    globex_run_id: UUID
    alice_key: str
    bob_key: str
    carol_key: str


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


@pytest.fixture
def api_client(db_url: str, engine: Engine, monkeypatch: MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("BEACON_DATABASE_URL", db_url)
    monkeypatch.setenv("BEACON_JWT_SIGNING_KEY", "test")
    monkeypatch.setenv("BEACON_OIDC_ISSUER", "https://test-issuer/")
    monkeypatch.setenv("BEACON_OIDC_CLIENT_ID", "beacon-test")
    monkeypatch.setenv("BEACON_OIDC_CLIENT_SECRET", "secret")
    monkeypatch.setenv("BEACON_OIDC_JWKS_URI", "https://test-issuer/jwks")

    import beacon_ui.api.deps as deps_mod

    deps_mod._factory = None

    from beacon_ui.api.app import create_app

    return TestClient(create_app())


def _issue_key(session: Session, user_id: UUID, label: str) -> str:
    key = generate_api_key(prefix="bcn_test")
    ApiKeyRepo(session).create(user_id=user_id, key_hash=hash_api_key(key), label=label)
    return key


@pytest.fixture
def world(session: Session) -> SeededWorld:
    """Seed a two-team world for REST, dashboard, and CLI tests."""
    from beacon_iam.service.teams import TeamService
    from beacon_storage.models.eval_items import EvalItemTier
    from beacon_storage.models.runs import HarnessMode
    from beacon_storage.models.tenancy import Role, ScopeKind
    from beacon_storage.repository.eval_items import EvalItemRepo
    from beacon_storage.repository.memberships import MembershipRepo
    from beacon_storage.repository.runs import RunRepo
    from beacon_storage.repository.solutions import SolutionRepo
    from beacon_storage.repository.suites import SuiteRepo

    user_service = UserService(session)
    team_service = TeamService(session)
    membership_repo = MembershipRepo(session)

    alice = user_service.upsert_from_oidc(
        OidcClaims(subject="alice", email="alice@example.com", name="Alice")
    )
    bob = user_service.upsert_from_oidc(
        OidcClaims(subject="bob", email="bob@example.com", name="Bob")
    )
    carol = user_service.upsert_from_oidc(
        OidcClaims(subject="carol", email="carol@example.com", name="Carol")
    )

    membership_repo.grant(
        user_id=alice.id,
        scope_kind=ScopeKind.GLOBAL,
        scope_id=alice.id,
        role=Role.BEACON_ADMIN,
    )
    session.flush()

    acme = team_service.create(actor_id=alice.id, name="acme")
    globex = team_service.create(actor_id=alice.id, name="globex")

    membership_repo.grant(
        user_id=alice.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=acme.id,
        role=Role.TEAM_ADMIN,
        granted_by=alice.id,
    )
    membership_repo.grant(
        user_id=bob.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=acme.id,
        role=Role.TEAM_MEMBER,
        granted_by=alice.id,
    )
    membership_repo.grant(
        user_id=carol.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=globex.id,
        role=Role.TEAM_ADMIN,
        granted_by=alice.id,
    )

    eval_item_repo = EvalItemRepo(session)
    item_ids = [
        eval_item_repo.create(
            tier=EvalItemTier.EXECUTION_CONFIRMED,
            suite="bird_minidev_v2",
            team_id=None,
            dataset_version="v2",
            item_input={"question": f"How many rows are in table {idx}?"},
            gold_answer={"sql": f"SELECT {idx}"},
            item_metadata={"difficulty": "easy", "seeded": True},
            created_by=alice.id,
        ).item_id
        for idx in range(1, 4)
    ]

    suite_repo = SuiteRepo(session)
    acme_suite = suite_repo.create(
        team_id=acme.id,
        name="bird_minidev_v2",
        description="Seeded BIRD Mini-Dev benchmark",
        method="manual",
        suite_metadata={"source": "seeded-world"},
        created_by=alice.id,
    )
    globex_suite = suite_repo.create(
        team_id=globex.id,
        name="bird_minidev_v2",
        description="Seeded BIRD Mini-Dev benchmark",
        method="manual",
        suite_metadata={"source": "seeded-world"},
        created_by=carol.id,
    )
    for suite in (acme_suite, globex_suite):
        suite_repo.add_items(suite_id=suite.id, item_ids=item_ids)

    solution_repo = SolutionRepo(session)
    acme_solution = solution_repo.create(
        team_id=acme.id,
        solution_id="chat-to-data-v3",
        version="0.1",
        owner_team=acme.id,
        summary="Seeded ACME SUT",
        supported_modes=["EVAL"],
        layers=[{"name": "ontology"}, {"name": "retry_loop"}],
        created_by=alice.id,
    )
    globex_solution = solution_repo.create(
        team_id=globex.id,
        solution_id="globex-research",
        version="0.1",
        owner_team=globex.id,
        summary="Seeded globex SUT",
        supported_modes=["EVAL"],
        layers=[{"name": "planner"}],
        created_by=carol.id,
    )

    run_repo = RunRepo(session)
    acme_run = run_repo.create(
        team_id=acme.id,
        suite_id=acme_suite.id,
        solution_id=acme_solution.id,
        suite="bird_minidev_v2",
        dataset_version="v2",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        config={"seeded": True},
        created_by=alice.id,
    )
    globex_run = run_repo.create(
        team_id=globex.id,
        suite_id=globex_suite.id,
        solution_id=globex_solution.id,
        suite="bird_minidev_v2",
        dataset_version="v2",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        config={"seeded": True},
        created_by=carol.id,
    )
    for run in (acme_run, globex_run):
        run_repo.mark_completed(run.id)

    alice_key = _issue_key(session, alice.id, "test-alice")
    bob_key = _issue_key(session, bob.id, "test-bob")
    carol_key = _issue_key(session, carol.id, "test-carol")
    session.commit()

    return SeededWorld(
        alice_id=alice.id,
        bob_id=bob.id,
        carol_id=carol.id,
        acme_team_id=acme.id,
        globex_team_id=globex.id,
        acme_suite_id=acme_suite.id,
        globex_suite_id=globex_suite.id,
        acme_solution_id=acme_solution.id,
        globex_solution_id=globex_solution.id,
        acme_run_id=acme_run.id,
        globex_run_id=globex_run.id,
        alice_key=alice_key,
        bob_key=bob_key,
        carol_key=carol_key,
    )


@pytest.fixture
def alice_api_key(session: Session) -> str:
    user = UserService(session).upsert_from_oidc(
        OidcClaims(subject="alice", email="alice@example.com", name="Alice")
    )
    key = generate_api_key(prefix="bcn_test")
    ApiKeyRepo(session).create(user_id=user.id, key_hash=hash_api_key(key), label="test")
    session.commit()
    return key


@pytest.fixture
def alice_team_membership(session: Session, alice_api_key: str) -> Team:
    """Ensure Alice has a team membership for trace ingestion."""
    from beacon_storage.models.tenancy import Role, ScopeKind
    from beacon_storage.repository.memberships import MembershipRepo
    from beacon_storage.repository.teams import TeamRepo

    _ = alice_api_key
    user = UserService(session).upsert_from_oidc(
        OidcClaims(subject="alice", email="alice@example.com", name="Alice")
    )
    team_repo = TeamRepo(session)
    team = team_repo.get_by_name("alice-team")
    if team is None:
        team = team_repo.create(name="alice-team")
    MembershipRepo(session).grant(
        user_id=user.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=team.id,
        role=Role.TEAM_ADMIN,
    )
    session.commit()
    return team


@pytest.fixture
def alice_suite(session: Session, alice_team_membership: Team) -> Suite:
    """Create a benchmark in Alice's team for suite-scoped API routes."""
    from beacon_storage.repository.suites import SuiteRepo

    user = UserService(session).upsert_from_oidc(
        OidcClaims(subject="alice", email="alice@example.com", name="Alice")
    )
    suite = SuiteRepo(session).create(
        team_id=alice_team_membership.id,
        name="alice-suite",
        description="",
        method="manual",
        suite_metadata={},
        created_by=user.id,
    )
    session.commit()
    return suite
