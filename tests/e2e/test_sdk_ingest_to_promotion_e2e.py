"""E2E: SDK trace ingest -> promotion chain -> registry query."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol, cast

import httpx
import pytest
from alembic import command
from alembic.config import Config
from beacon_iam.auth.api_key import generate_api_key, hash_api_key
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_registry.items import ItemService
from beacon_registry.types import ActorType, EvalItemTier
from beacon_sdk import BeaconSyncClient
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.api_keys import ApiKeyRepo
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.production_traces import ProductionTraceRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.provenance import ProvenanceRepo
from beacon_storage.repository.teams import TeamRepo
from fastapi.testclient import TestClient
from pytest_postgresql import factories
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from beacon_storage.models.tenancy import Project, Team
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
def api_client(db_url: str, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("BEACON_DATABASE_URL", db_url)
    monkeypatch.setenv("BEACON_JWT_SIGNING_KEY", "test")
    monkeypatch.setenv("BEACON_OIDC_ISSUER", "https://test-issuer/")
    monkeypatch.setenv("BEACON_OIDC_CLIENT_ID", "beacon-test")
    monkeypatch.setenv("BEACON_OIDC_CLIENT_SECRET", "secret")
    monkeypatch.setenv("BEACON_OIDC_JWKS_URI", "https://test-issuer/jwks")

    _ = engine
    import beacon_ui.api.deps as deps_mod

    deps_mod._factory = None

    from beacon_ui.api.app import create_app

    return TestClient(create_app())


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
        role=Role.TEAM_MEMBER,
    )
    session.commit()
    return team


@pytest.fixture
def alice_project(session: Session, alice_team_membership: Team) -> Project:
    user = UserService(session).upsert_from_oidc(
        OidcClaims(subject="alice", email="alice@example.com", name="Alice")
    )
    project = ProjectRepo(session).create(
        team_id=alice_team_membership.id,
        name="alice-proj",
        created_by=user.id,
    )
    MembershipRepo(session).grant(
        user_id=user.id,
        scope_kind=ScopeKind.PROJECT,
        scope_id=project.id,
        role=Role.PROJECT_OWNER,
        granted_by=user.id,
    )
    session.commit()
    return project


@pytest.mark.integration
@pytest.mark.e2e
def test_sdk_ingest_to_promotion_chain(
    api_client: TestClient,
    alice_api_key: str,
    alice_team_membership: Team,
    alice_project: Project,
    session: Session,
) -> None:
    user = UserService(session).upsert_from_oidc(
        OidcClaims(subject="alice", email="alice@example.com", name="Alice")
    )

    transport = httpx.ASGITransport(app=api_client.app)
    with BeaconSyncClient(
        base_url="http://testserver",
        api_key=alice_api_key,
        transport=transport,
        drain_interval_seconds=0.01,
        request_timeout_seconds=2.0,
    ) as client:
        accepted = client.log_trace_sync(
            solution_id="acme-chat-to-data",
            project_id=alice_project.id,
            item_input={"question": "How many students?", "db": "california_schools"},
            item_output={"sql": "SELECT count(*) FROM students", "answer": "1230"},
            trace={"name": "root", "level": "workflow", "children": []},
            metadata={"latency_ms": 234, "cost_usd": 0.012},
            is_eval_candidate=True,
        )
        assert accepted is True
        assert client.flush(timeout=2.0) is True

    production_traces = [
        trace
        for trace in ProductionTraceRepo(session).list_unprocessed()
        if trace.solution_id == "acme-chat-to-data" and trace.project_id == alice_project.id
    ]
    assert len(production_traces) == 1
    production_trace = production_traces[0]
    assert production_trace.is_eval_candidate is True
    assert production_trace.team_id == alice_team_membership.id

    item_id = ItemService(session).create_item(
        tier=EvalItemTier.MODEL_PROPOSED,
        suite="acme-prod-2026q2",
        team_id=alice_team_membership.id,
        solution_id="acme-chat-to-data",
        dataset_version="v2026-06-05",
        item_input=production_trace.item_input,
        gold_answer=None,
        item_metadata={
            "source": "production_trace",
            "production_trace_id": str(production_trace.id),
        },
        created_by=user.id,
    )
    ProvenanceRepo(session).append(
        item_id=item_id,
        team_id=alice_team_membership.id,
        prior_tier=None,
        new_tier=EvalItemTier.MODEL_PROPOSED,
        actor_type=ActorType.SYSTEM,
        actor_id="e2e_test_simulating_p5_worker",
        created_by=None,
        reason="ingested from production trace",
        evidence={"production_trace_id": str(production_trace.id)},
    )
    session.commit()

    execution_response = api_client.post(
        f"/v1/registry/items/{item_id}/promote",
        params={"project_id": str(alice_project.id)},
        json={
            "new_tier": "execution_confirmed",
            "reason": "3 SUTs converged on '1230'",
            "evidence": {"converging_run_ids": []},
        },
        headers={"X-API-Key": alice_api_key},
    )
    assert execution_response.status_code == 200, execution_response.text
    assert execution_response.json()["prior_tier"] == "model_proposed"
    assert execution_response.json()["new_tier"] == "execution_confirmed"

    queue_response = api_client.get(
        f"/v1/projects/{alice_project.id}/review-queue?suite=acme-prod-2026q2",
        headers={"X-API-Key": alice_api_key},
    )
    assert queue_response.status_code == 200, queue_response.text
    queue = queue_response.json()
    assert queue["total"] == 1
    assert queue["items"][0]["item_id"] == str(item_id)

    human_response = api_client.post(
        f"/v1/registry/items/{item_id}/promote",
        params={"project_id": str(alice_project.id)},
        json={
            "new_tier": "human_verified",
            "reason": "reviewed; SQL is correct; answer matches",
            "evidence": {"reviewer_notes": "ran SQL by hand"},
        },
        headers={"X-API-Key": alice_api_key},
    )
    assert human_response.status_code == 200, human_response.text
    assert human_response.json()["prior_tier"] == "execution_confirmed"
    assert human_response.json()["new_tier"] == "human_verified"

    registry_response = api_client.get(
        "/v1/registry/items?suite=acme-prod-2026q2&tier=human_verified",
        headers={"X-API-Key": alice_api_key},
    )
    assert registry_response.status_code == 200, registry_response.text
    registry_body = registry_response.json()
    assert registry_body["total"] == 1
    assert registry_body["items"][0]["item_id"] == str(item_id)

    empty_queue_response = api_client.get(
        f"/v1/projects/{alice_project.id}/review-queue?suite=acme-prod-2026q2",
        headers={"X-API-Key": alice_api_key},
    )
    assert empty_queue_response.status_code == 200, empty_queue_response.text
    assert empty_queue_response.json()["total"] == 0

    events = ProvenanceRepo(session).list_for_item(item_id)
    assert len(events) == 3
    assert events[0].prior_tier is None
    assert events[0].new_tier == EvalItemTier.MODEL_PROPOSED
    assert events[0].actor_type == ActorType.SYSTEM
    assert events[1].prior_tier == EvalItemTier.MODEL_PROPOSED
    assert events[1].new_tier == EvalItemTier.EXECUTION_CONFIRMED
    assert events[2].prior_tier == EvalItemTier.EXECUTION_CONFIRMED
    assert events[2].new_tier == EvalItemTier.HUMAN_VERIFIED
    assert events[2].actor_type == ActorType.HUMAN
    assert events[2].created_by == user.id
