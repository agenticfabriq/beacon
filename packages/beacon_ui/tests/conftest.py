"""Shared API integration fixtures."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

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
    from fastapi import FastAPI
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


def _constrain_to_serving_role(app: FastAPI) -> None:
    """Make every request through ``app`` run as ``beacon_app``.

    Lifted out of ``constrained_client`` so ``api_client`` can opt into the
    same thing, so RLS coverage stops being the six routes one file exercises
    by hand. See ``constrained_client`` for why each half of this is here;
    nothing in it changed when it moved.

    Run the whole suite the way production will connect after the DSN switch::

        BEACON_TEST_CONSTRAINED=1 make test

    **Be exact about what that covers, because the run's own total is
    misleading.** The flag reaches requests that go through this override --
    239 test functions across 34 files, all of them in ``packages/beacon_ui``.
    The rest of the repo builds its own sessions from ``DATABASE_URL``, which
    the Makefile points at the OWNING role, so those consult no policy whatever
    the flag says. Measured: 1377 items collected repo-wide, 335 of them in
    ``beacon_ui`` and 1042 outside it.

    So this is real coverage of the ROUTES and is not the switch validated
    suite-wide -- a green 1377 mostly reflects tests that never met a policy.
    (An earlier version of this note said 74 rather than 239, from a grep that
    only matched single-line signatures. It understated the reach threefold,
    which is the opposite error from the one the paragraph is warning about.)

    **It is green in both modes.** 1377 passed, 2 skipped, with the flag set and
    with it unset -- so this is a gate for the switch, not a list of known
    failures to remember.

    It did not start that way. The first run reported three failures, all one
    finding: a non-member touching a team was answered ``404`` where the test
    pinned ``403``. Not a broken route -- ``require_permission`` resolves the
    scope with ``TeamRepo.get`` and raises 404 when it comes back None, and
    under the serving role a team row is invisible to someone holding no
    membership in it, so the 404 fires before the permission check.

    404 is the answer we took, because 403 confirms the team is real to
    somebody with no business knowing: walking a list of ids would reveal which
    exist. It costs a legitimate user nothing, which was measured rather than
    assumed::

        member lacking the permission:  403 as owner, 403 as beacon_app
        true outsider:                  403 as owner, 404 as beacon_app

    A member can still SEE the team, so they reach the check and still get the
    accurate 403. Only a true outsider falls to 404. Those tests now pin each
    role's own answer, parametrized over ``owner_client`` and
    ``constrained_client``, rather than asserting one status that is right on
    only one side of the switch -- except the e2e flow, whose subject is the
    flow and which accepts either, pointing at the two that pin it exactly.
    """
    import beacon_ui.api.deps as deps_mod
    from beacon_storage.rls import bind_rls
    from beacon_ui.api.deps import get_session
    from sqlalchemy import event

    def constrained_session() -> Iterator[Session]:
        factory = deps_mod._get_factory()
        session = factory()
        try:
            # `bind_rls` FIRST, exactly as `get_session` does. This override
            # replaces that dependency wholesale, so anything production wires
            # up has to be wired up here too -- an earlier version omitted it,
            # which meant deleting the call from deps.py left the whole suite
            # green while production lost the acting user after every
            # mid-request commit.
            bind_rls(session)

            # The ROLE on every transaction, not just the first. `SET LOCAL
            # ROLE` is transaction-scoped, so a route that commits partway --
            # delete_team, remove_team_member and issue_member_key all do --
            # reverted this connection to the OWNER for everything after,
            # where production stays constrained because its role comes from
            # the DSN. Re-applying on `after_begin` makes the fixture behave
            # the way production does instead of being weaker than it in the
            # one place that matters.
            @event.listens_for(session, "after_begin")
            def _constrain(_session: Session, _transaction: object, connection: Any) -> None:
                connection.execute(text("SET LOCAL ROLE beacon_app"))

            # And once now, because the listener fires on the NEXT begin and
            # the first statement below is what proves it took.
            session.execute(text("SET LOCAL ROLE beacon_app"))
            # And PROVE it took. Every route-level assertion in
            # test_routes_under_rls.py is worthless if this silently did not
            # happen, and the route-level checks could not detect it: they read
            # endpoints that filter in Python, so they pass either way.
            # Measured -- deleting the line above left all six of them green.
            role = session.execute(text("SELECT current_user")).scalar()
            assert role == "beacon_app", (
                f"the constrained client is running as {role!r}; every RLS "
                "assertion made through it would be vacuous"
            )
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_session] = constrained_session


def _build_app(db_url: str, monkeypatch: MonkeyPatch) -> FastAPI:
    """The app the client fixtures share, with the test environment applied."""
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

    return create_app()


def _constrained_mode() -> bool:
    """Whether ``BEACON_TEST_CONSTRAINED`` asks for the serving role.

    An explicit off-list rather than truthiness: ``BEACON_TEST_CONSTRAINED=0``
    used to turn the mode ON, so an operator setting it to DISABLE the mode met
    the very failures the mode is documented to cause.
    """
    return os.environ.get("BEACON_TEST_CONSTRAINED", "").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
        "off",
    )


@pytest.fixture
def api_client(db_url: str, engine: Engine, monkeypatch: MonkeyPatch) -> TestClient:
    app = _build_app(db_url, monkeypatch)
    # Opt in to running the ORDINARY client under the serving role, so the
    # routes can be re-run the way production will connect after the DSN
    # switch. OFF by default: unset, this fixture behaves exactly as it always
    # has, so CI and every existing test are untouched.
    if _constrained_mode():
        _constrain_to_serving_role(app)
    return TestClient(app)


@pytest.fixture
def owner_client(db_url: str, engine: Engine, monkeypatch: MonkeyPatch) -> TestClient:
    """``api_client`` pinned to the OWNING role, whatever the flag says.

    For a test whose point is the CONTRAST between the two roles. Those
    parametrize over ``["owner_client", "constrained_client"]``, and if the
    first of those quietly became constrained too, both parameters would run
    the same thing: ten green items proving half of what they claim, with
    nothing in the file able to notice. `api_client` cannot serve that purpose
    precisely because the flag is allowed to change it.
    """
    return TestClient(_build_app(db_url, monkeypatch))


@pytest.fixture
def constrained_client(db_url: str, engine: Engine, monkeypatch: MonkeyPatch) -> TestClient:
    """An API client whose sessions run as ``beacon_app``, the SERVING role.

    ``api_client`` above connects as the owning role, which carries
    ``rolbypassrls`` -- so no test using it consults a policy, and none can
    observe the DSN switch the Makefile makes. That gap is where two real bugs
    lived: ``delete_team`` orphaned every membership because a SELECT policy
    filtered its purge, and ``add_team_member`` returned 500 because an
    invitee it could not read looked like a new account. Both read correctly at
    the policy level and broke at the route.

    ``TEST_DATABASE_URL`` cannot simply name ``beacon_app``: the fixtures drop
    and recreate the public schema, which the serving role must not be able to
    do. So the connection is made as the owner and each request DROPS to the
    serving role.

    ``SET LOCAL ROLE`` is scoped to the TRANSACTION, not the session -- an
    earlier version of this docstring said session, which is wrong and
    mattered. A route that commits partway through reverted this connection to
    the OWNER for everything after, where production stays constrained because
    its role comes from the DSN. So the fixture was WEAKER than production at
    exactly the point a mid-request commit happens, and a test of a route that
    read after committing would have passed here for the wrong reason.

    Both halves are re-applied on every transaction now: the role by a listener
    here, the acting user by ``bind_rls`` -- which this override registers
    because it replaces ``get_session`` wholesale, so anything production wires
    up has to be wired up here too.

    ``delete_team``, ``remove_team_member`` and ``issue_member_key`` all commit
    partway; each commits as its last statement today, so nothing read past the
    revert while it existed.
    """
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

    app = create_app()

    _constrain_to_serving_role(app)
    return TestClient(app)


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
