"""Materialize gold answers: execute each item's gold SQL once, store the rows.

The one place execution still happens in the gold pipeline. Run it at import
time (or as a backfill) against the benchmark's reference engine; after it,
grading is pure comparison and the benchmark database is not a runtime
dependency of anything.

Usage::

    DATABASE_URL=... uv run python scripts/materialize_gold.py \
        --suite bird_minidev_v2 \
        --engine-url postgresql+psycopg://user:pw@host:5432/bird_dev \
        [--force]

Items whose gold already carries rows are skipped unless --force. Failures
are reported per item and do not stop the sweep -- a gold query that cannot
run is a curation problem to surface, not to hide.
"""

from __future__ import annotations

import argparse
import os
import sys

import sqlalchemy as sa
from beacon_graders.comparison import canonicalize_cell
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.models.eval_items import EvalItem

# BIRD's own cap on gold result sets; larger golds are stored truncated with
# the true count, and the grader's containment reading handles the rest.
MAX_GOLD_ROWS = 1000


def _execute_gold(
    engine: sa.Engine, sql: str, timeout_seconds: int = 60
) -> tuple[list[str], list[list[object]], int]:
    """Run one gold query, returning canonical (columns, rows, true row count)."""
    if engine.dialect.paramstyle in ("pyformat", "format"):
        sql = sql.replace("%", "%%")
    with engine.connect() as connection, connection.begin():
        if engine.dialect.name == "postgresql" and timeout_seconds > 0:
            connection.exec_driver_sql(f"SET LOCAL statement_timeout = {timeout_seconds * 1000}")
        cursor = connection.exec_driver_sql(sql)
        columns = [str(key) for key in cursor.keys()]  # noqa: SIM118
        fetched = cursor.fetchall()
    rows = [[canonicalize_cell(cell) for cell in row] for row in fetched[:MAX_GOLD_ROWS]]
    return columns, rows, len(fetched)


def materialize_suite(
    session_factory: sa.orm.sessionmaker[sa.orm.Session],
    *,
    suite: str,
    engine: sa.Engine,
    force: bool = False,
) -> dict[str, int]:
    """Materialize every active item of ``suite``; return a summary of counts."""
    summary = {"materialized": 0, "skipped": 0, "failed": 0, "no_sql": 0}
    with session_factory() as session:
        items = list(
            session.scalars(
                sa.select(EvalItem).where(
                    EvalItem.suite == suite,
                    EvalItem.valid_to.is_(None),
                )
            )
        )
        print(f"{len(items)} active items in suite {suite!r}")
        for item in items:
            gold = dict(item.gold_answer or {})
            sql = gold.get("sql")
            if not sql:
                summary["no_sql"] += 1
                continue
            if gold.get("rows") is not None and not force:
                summary["skipped"] += 1
                continue
            try:
                columns, rows, row_count = _execute_gold(engine, str(sql))
            except Exception as exc:  # noqa: BLE001 - report and continue
                summary["failed"] += 1
                print(f"  FAILED {item.item_id}: {str(exc)[:160]}")
                continue
            gold["columns"] = columns
            gold["rows"] = rows
            gold["row_count"] = row_count
            gold["materialized_from"] = engine.dialect.name
            item.gold_answer = gold
            summary["materialized"] += 1
        session.commit()
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="suite name whose items to materialize")
    parser.add_argument(
        "--engine-url", required=True, help="SQLAlchemy URL of the reference engine"
    )
    parser.add_argument(
        "--force", action="store_true", help="re-materialize items that already carry rows"
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    storage_engine = make_engine(database_url)
    benchmark_engine = sa.create_engine(args.engine_url)
    try:
        summary = materialize_suite(
            make_session_factory(storage_engine),
            suite=args.suite,
            engine=benchmark_engine,
            force=args.force,
        )
    finally:
        benchmark_engine.dispose()
        storage_engine.dispose()

    print(
        f"materialized={summary['materialized']} skipped={summary['skipped']} "
        f"failed={summary['failed']} no_sql={summary['no_sql']}"
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
