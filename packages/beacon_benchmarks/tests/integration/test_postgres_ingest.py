"""SQLite to Postgres ingest integration tests."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from beacon_benchmarks.ingest.postgres import ingest_sqlite_to_postgres
from sqlalchemy import text

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine


pytestmark = pytest.mark.integration


@pytest.fixture
def tiny_sqlite(tmp_path: Path) -> Path:
    path = tmp_path / "tiny.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE parents (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE children (id INTEGER PRIMARY KEY, parent_id INTEGER, age REAL);
        INSERT INTO parents VALUES (1, 'Alice'), (2, 'Bob');
        INSERT INTO children VALUES (10, 1, 5.5), (11, 2, 7.0);
        """
    )
    conn.commit()
    conn.close()
    return path


def test_ingest_creates_tables_and_loads_rows(
    db_url: str, tiny_sqlite: Path, engine: Engine
) -> None:
    summary = ingest_sqlite_to_postgres(
        sqlite_path=tiny_sqlite,
        postgres_url=db_url,
        target_schema="public",
        benchmark_name="testbench",
        dataset_version="v1",
        suite="testbench_v1",
    )
    assert summary["row_counts"]["parents"] == 2
    assert summary["row_counts"]["children"] == 2
    assert any(
        edge["from_table"] == "children" and edge["to_table"] == "parents"
        for edge in summary["fks"]
    )

    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM children")).scalar()
    assert count == 2


def test_ingest_records_dataset_load_row(db_url: str, tiny_sqlite: Path, engine: Engine) -> None:
    summary = ingest_sqlite_to_postgres(
        sqlite_path=tiny_sqlite,
        postgres_url=db_url,
        target_schema="public",
        benchmark_name="testbench",
        dataset_version="v1",
        suite="testbench_v1",
    )

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT suite, source_sha256, target_schema FROM dataset_loads "
                "WHERE suite = 'testbench_v1' ORDER BY created_at DESC LIMIT 1"
            )
        ).fetchone()

    assert row is not None
    assert row[0] == "testbench_v1"
    assert len(row[1]) == 64
    assert summary["dataset_load_id"] is not None
