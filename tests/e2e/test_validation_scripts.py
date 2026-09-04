"""E2E checks for validation helper scripts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING, Protocol, cast

import pytest
from alembic import command
from alembic.config import Config
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.models.attribution import Attribution
from pytest_postgresql import factories
from sqlalchemy import func, select, text

if TYPE_CHECKING:
    from collections.abc import Iterator

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


def test_sweep_script_runs_dummy_nightly_loo(db_url: str, session: Session) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/sweep.py",
            "--sut",
            "dummy",
            "--suite",
            "test_suite",
            "--pass-num",
            "1",
            "--tasks",
            "5",
            "--mode",
            "NIGHTLY_LOO",
        ],
        env={**os.environ, "DATABASE_URL": db_url},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["attribution_rows"] == 2
    assert payload["tasks"] == 5
    assert payload["pass_num"] == 1
    assert set(payload["layers"]) == {"ontology", "retry_loop"}
    assert session.scalar(select(func.count()).select_from(Attribution)) == 2

    # What the delta was measured over, in the output a human reads (B64). The
    # count must be an INT: read through the float helper beside it, it prints
    # 2.0, and an absent one prints 0.0 -- indistinguishable from "nothing was
    # compared". Nothing else in the suite executes these scripts, so without
    # this, swapping the reader back leaves every test green.
    for layer, summary in payload["layers"].items():
        assert summary["n_items_submitted"] == 5, layer
        assert summary["n_compared"] == 5, layer
        assert isinstance(summary["n_compared"], int), layer
        assert not isinstance(summary["n_compared"], bool), layer
        assert summary["n_baseline_excluded"] == 0, layer
        assert summary["n_ablated_excluded"] == 0, layer
