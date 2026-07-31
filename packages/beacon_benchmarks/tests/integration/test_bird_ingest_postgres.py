"""BIRD ingest into per-database Postgres schemas."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from beacon_benchmarks.bird_minidev.ingest_bird import ingest_bird_dbs
from sqlalchemy import text

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine


pytestmark = pytest.mark.integration


@pytest.fixture
def bird_root(tmp_path: Path) -> Path:
    """Synthesize a minimal BIRD layout with two SQLite databases."""
    root = tmp_path / "bird"
    for db_id in ("test_california_schools", "test_card_games"):
        db_dir = root / "mini_dev_data" / "dev_databases" / db_id
        db_dir.mkdir(parents=True)
        sqlite_path = db_dir / f"{db_id}.sqlite"
        conn = sqlite3.connect(sqlite_path)
        conn.executescript(
            """
            CREATE TABLE schools (
                "CDSCode" TEXT PRIMARY KEY,
                "County" TEXT,
                "EnrollK12" INTEGER
            );
            CREATE TABLE frpm (
                "CDSCode" TEXT,
                "Free Meal Count (K-12)" INTEGER,
                "Enrollment (K-12)" REAL
            );
            INSERT INTO schools VALUES ('001', 'Alameda', 1200);
            INSERT INTO frpm VALUES ('001', 300, 1200.0);
            """
        )
        conn.commit()
        conn.close()
    return root


def test_ingest_creates_one_schema_per_db(db_url: str, bird_root: Path, engine: Engine) -> None:
    summary = ingest_bird_dbs(
        raw_root=bird_root,
        target_db_url=db_url,
        selected_dbs=["test_california_schools", "test_card_games"],
    )

    assert set(summary["dbs"]) == {"test_california_schools", "test_card_games"}
    with engine.connect() as conn:
        school_count = conn.execute(
            text('SELECT COUNT(*) FROM "bird_test_california_schools".schools')
        ).scalar()
        meal_count = conn.execute(
            text(
                'SELECT COUNT(*) FROM "bird_test_california_schools".frpm '
                'WHERE "Free Meal Count (K-12)" = 300'
            )
        ).scalar()

    assert school_count == 1
    assert meal_count == 1


def test_ingest_skips_missing_dbs_with_warning(db_url: str, bird_root: Path) -> None:
    summary = ingest_bird_dbs(
        raw_root=bird_root,
        target_db_url=db_url,
        selected_dbs=["test_california_schools", "nonexistent_db"],
    )

    assert "test_california_schools" in summary["dbs"]
    assert "nonexistent_db" in summary["skipped"]
