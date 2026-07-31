"""Shared E2E fixtures: per-test fresh DB with migrations applied.

Matches the `test_validation_scripts.py` pattern — drops/recreates ``public``
and re-runs Alembic before each test so e2e tests do not inherit state (or
destruction) from package-level integration tests. This conftest only
exposes ``db_url``/``engine``/``session`` to tests that do **not** already
declare their own module-level fixtures of the same name (pytest's file-local
fixtures shadow conftest ones).

We deliberately do **not** declare a top-level ``postgresql_proc`` fixture
here because several e2e files declare their own; ours would conflict. We
require ``DATABASE_URL`` to be set (the root ``conftest.py`` sets a default).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from beacon_storage.db import make_engine, make_session_factory
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


MIGRATIONS_DIR = "packages/beacon_storage/src/beacon_storage/migrations"


@pytest.fixture
def db_url() -> str:
    """Return ``$DATABASE_URL``; root conftest seeds it if unset."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required for tests/e2e/")
    return url


@pytest.fixture
def engine(db_url: str) -> Iterator[Engine]:
    """Drop+recreate ``public``, apply Alembic head, yield engine, repeat on teardown."""
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
    """Yield a SQLAlchemy session bound to the per-test engine."""
    factory = make_session_factory(engine)
    with factory() as session:
        yield session
