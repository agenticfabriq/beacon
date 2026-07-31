"""Shared fixtures for benchmark adapter tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import pytest
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.models import Base
from pytest_postgresql import factories

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


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
    if database_url := os.environ.get("DATABASE_URL"):
        return database_url
    postgresql = cast("_PostgresConnection", request.getfixturevalue("postgresql"))
    info = postgresql.info
    return f"postgresql+psycopg://{info.user}@{info.host}:{info.port}/{info.dbname}"


@pytest.fixture
def engine(db_url: str) -> Iterator[Engine]:
    eng = make_engine(db_url)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = make_session_factory(engine)
    with factory() as s:
        yield s


@pytest.fixture
def beacon_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate ``BEACON_DATA_DIR`` per test."""
    data_dir = tmp_path / "beacon-data"
    data_dir.mkdir()
    monkeypatch.setenv("BEACON_DATA_DIR", str(data_dir))
    return data_dir


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent.parent / "src" / "beacon_benchmarks" / "regression" / "fixtures"
