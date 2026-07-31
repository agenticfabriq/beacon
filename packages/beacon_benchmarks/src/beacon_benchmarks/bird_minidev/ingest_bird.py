"""Ingest selected BIRD Mini-Dev SQLite databases into Postgres schemas."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from beacon_benchmarks.ingest.postgres import ingest_sqlite_to_postgres

if TYPE_CHECKING:
    from pathlib import Path


log = logging.getLogger(__name__)


def ingest_bird_dbs(
    *,
    raw_root: Path,
    target_db_url: str,
    selected_dbs: list[str],
) -> dict[str, Any]:
    """Ingest each selected BIRD DB into its own ``bird_<db_id>`` schema."""
    db_dir_root = raw_root / "mini_dev_data" / "dev_databases"
    loaded: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []

    for db_id in selected_dbs:
        sqlite_path = db_dir_root / db_id / f"{db_id}.sqlite"
        if not sqlite_path.exists():
            log.warning("BIRD SQLite not found for %s at %s; skipping", db_id, sqlite_path)
            skipped.append(db_id)
            continue

        loaded[db_id] = ingest_sqlite_to_postgres(
            sqlite_path=sqlite_path,
            postgres_url=target_db_url,
            target_schema=f"bird_{db_id}",
            benchmark_name="bird_minidev",
            dataset_version="v2-2025-07-22",
            suite="bird_minidev_v2",
        )

    return {"dbs": list(loaded), "skipped": skipped, "loads": loaded}
