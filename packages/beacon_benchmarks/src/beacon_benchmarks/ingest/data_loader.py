"""Bulk-load SQLite rows into Postgres."""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING, Any, cast

from beacon_benchmarks.ingest.schema_translator import quote_identifier

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator

    from sqlalchemy import Connection

_NULL_SENTINEL = "\\N"


def iter_sqlite_rows(sqlite_conn: sqlite3.Connection, table: str) -> Iterator[tuple[Any, ...]]:
    """Yield rows from a SQLite table in batches."""
    cursor = sqlite_conn.execute(f'SELECT * FROM "{table}"')  # noqa: S608
    while True:
        rows = cursor.fetchmany(10_000)
        if not rows:
            break
        yield from rows


def _csv_copy_value(value: Any) -> Any:
    if value is None:
        return _NULL_SENTINEL
    return value


def copy_table_into_postgres(
    *,
    sqlite_conn: sqlite3.Connection,
    pg_conn: Connection,
    table: str,
    columns: list[str],
) -> int:
    """Use psycopg ``COPY FROM STDIN`` to bulk-load a table."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL)
    row_count = 0
    for row in iter_sqlite_rows(sqlite_conn, table):
        writer.writerow([_csv_copy_value(value) for value in row])
        row_count += 1
    buffer.seek(0)

    raw = cast("Any", pg_conn.connection).dbapi_connection
    col_list = ", ".join(quote_identifier(col) for col in columns)
    copy_sql = (
        f"COPY {quote_identifier(table)} ({col_list}) FROM STDIN "
        "WITH (FORMAT CSV, NULL '\\N', QUOTE '\"', ESCAPE '\"')"
    )
    with raw.cursor() as cursor, cursor.copy(copy_sql) as copy:
        copy.write(buffer.getvalue())
    return row_count
