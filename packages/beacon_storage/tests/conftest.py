"""Shared fixtures: Postgres via pytest-postgresql, engine, session."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol, cast

import pytest
from alembic import command
from alembic.config import Config
from beacon_storage.db import make_engine, make_session_factory
from pytest_postgresql import factories
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

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
    eng = make_engine(db_url)
    with eng.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    cfg = Config(f"{MIGRATIONS_DIR}/alembic.ini")
    cfg.set_main_option("script_location", MIGRATIONS_DIR)
    command.upgrade(cfg, "head")
    # The serving role is NOT provisioned here. 0025 creates it and grants it,
    # so letting the migration do it means these fixtures exercise the real
    # provisioning path rather than a parallel one that could drift from it --
    # and the parallel one did: the fixture created the role without LOGIN,
    # which is cluster-wide, so 0025's IF-NOT-EXISTS guard skipped its own
    # CREATE and the app could not authenticate. Dropping the role here is also
    # no longer possible once it holds grants.
    yield eng
    with eng.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = make_session_factory(engine)
    with factory() as s:
        yield s
