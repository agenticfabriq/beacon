"""Top-level SQLite to Postgres ingest entry point."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from beacon_storage.db import make_session_factory
from beacon_storage.repository.dataset_loads import DatasetLoadRepo
from sqlalchemy import create_engine, text

from beacon_benchmarks.base.errors import IngestError
from beacon_benchmarks.ingest.data_loader import copy_table_into_postgres
from beacon_benchmarks.ingest.fk_reconstruct import detect_implicit_fks, emit_alter_constraints
from beacon_benchmarks.ingest.schema_translator import quote_identifier, translate_create_table

if TYPE_CHECKING:
    from pathlib import Path


def _sha256_sqlite(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_sqlite_schema(conn: sqlite3.Connection) -> dict[str, str]:
    cursor = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    return {row[0]: row[1] for row in cursor.fetchall() if row[1]}


def _read_sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cursor = conn.execute(f'PRAGMA table_info("{table}")')
    return [row[1] for row in cursor.fetchall()]


def _quote_exact_identifier(name: str) -> str:
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def ingest_sqlite_to_postgres(
    *,
    sqlite_path: Path,
    postgres_url: str,
    target_schema: str,
    benchmark_name: str,
    dataset_version: str,
    suite: str,
) -> dict[str, Any]:
    """Translate and ingest one SQLite file into a Postgres database."""
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    engine = create_engine(postgres_url, future=True)
    source_sha = _sha256_sqlite(sqlite_path)

    try:
        schema = _read_sqlite_schema(sqlite_conn)
        if not schema:
            raise IngestError(f"no user tables in {sqlite_path}")

        translated: dict[str, str] = {}
        for table, ddl in schema.items():
            try:
                translated[table] = translate_create_table(ddl)
            except Exception as exc:  # noqa: BLE001
                raise IngestError(f"DDL translation failed for {table}: {exc}") from exc

        with engine.begin() as pg:
            if target_schema != "public":
                pg.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{target_schema}"'))
                pg.execute(text(f'SET LOCAL search_path TO "{target_schema}"'))
            for table, pg_ddl in translated.items():
                pg.execute(text(f"DROP TABLE IF EXISTS {quote_identifier(table)} CASCADE"))
                pg.execute(text(f"DROP TABLE IF EXISTS {_quote_exact_identifier(table)} CASCADE"))
                pg.execute(text(pg_ddl))

        row_counts: dict[str, int] = {}
        with engine.begin() as pg:
            if target_schema != "public":
                pg.execute(text(f'SET LOCAL search_path TO "{target_schema}"'))
            for table in schema:
                columns = _read_sqlite_columns(sqlite_conn, table)
                row_counts[table] = copy_table_into_postgres(
                    sqlite_conn=sqlite_conn,
                    pg_conn=pg,
                    table=table,
                    columns=columns,
                )

        col_map = {table: _read_sqlite_columns(sqlite_conn, table) for table in schema}
        fk_edges = detect_implicit_fks(col_map)
        with engine.begin() as pg:
            if target_schema != "public":
                pg.execute(text(f'SET LOCAL search_path TO "{target_schema}"'))
            for stmt in emit_alter_constraints(fk_edges):
                with suppress(Exception):
                    pg.execute(text(stmt))

        with engine.begin() as pg:
            if target_schema != "public":
                pg.execute(text(f'SET LOCAL search_path TO "{target_schema}"'))
            for table in schema:
                pg.execute(text(f"ANALYZE {quote_identifier(table)}"))

        factory = make_session_factory(engine)
        with factory() as session:
            dataset_load = DatasetLoadRepo(session).record(
                benchmark_name=benchmark_name,
                suite=suite,
                dataset_version=dataset_version,
                source_path=str(sqlite_path),
                target_schema=target_schema,
                row_counts=row_counts,
                source_sha256=source_sha,
            )
            session.commit()
            dataset_load_id = str(dataset_load.id)

        return {
            "row_counts": row_counts,
            "fks": [
                {
                    "from_table": edge.from_table,
                    "from_col": edge.from_col,
                    "to_table": edge.to_table,
                    "to_col": edge.to_col,
                }
                for edge in fk_edges
            ],
            "source_sha256": source_sha,
            "dataset_load_id": dataset_load_id,
        }
    finally:
        sqlite_conn.close()
        engine.dispose()
