"""`beacon audit` commands: optional integrity checks over pushed evidence.

Grading never executes SQL; this does, on request, to spot-check that pushed
rows agree with what the SQL actually returns on a given engine. An integrity
tool for an operator with a database at hand -- never a serving dependency.
"""

from __future__ import annotations

import json
import os
import random
from uuid import UUID

import click
import sqlalchemy as sa
from beacon_graders.comparison import canonicalize_rows, compare_rows
from beacon_graders.tolerance import Tolerance


@click.group()
def cli() -> None:
    """Integrity checks that re-execute pushed SQL on demand."""


@cli.command("spot-check")
@click.option("--run", "run_id", required=True, help="Run UUID whose results to check.")
@click.option("--db-url", required=True, help="SQLAlchemy URL of the engine to execute against.")
@click.option("--sample", default=25, show_default=True, type=int, help="Results to check.")
@click.option("--seed", default=0, show_default=True, type=int, help="Sampling seed.")
def spot_check(run_id: str, db_url: str, sample: int, seed: int) -> None:
    """Re-execute a sample of a run's pushed SQL and compare with pushed rows.

    A divergence means the pushed evidence disagrees with the SQL on this
    engine -- dialect drift, data drift, or a fabricated push. It is reported,
    not scored: verdicts stay what the comparison against gold said.
    """
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise click.ClickException("DATABASE_URL is required")

    storage = sa.create_engine(database_url)
    with storage.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT item_id, output FROM results "
                "WHERE run_id = :run AND output ? 'rows' AND output ->> 'sql' IS NOT NULL"
            ),
            {"run": UUID(run_id)},
        ).fetchall()
    storage.dispose()
    if not rows:
        click.echo("no results with pushed rows and SQL in this run")
        return

    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)  # noqa: S311 - sampling, not cryptography
    picked = shuffled[:sample]
    engine = sa.create_engine(db_url)
    tolerance = Tolerance()
    diverged = errored = agreed = 0
    try:
        for item_id, output in picked:
            payload = output if isinstance(output, dict) else json.loads(output)
            sql = str(payload["sql"])
            if engine.dialect.paramstyle in ("pyformat", "format"):
                sql = sql.replace("%", "%%")
            try:
                with engine.connect() as connection, connection.begin():
                    cursor = connection.exec_driver_sql(sql)
                    executed = canonicalize_rows([tuple(row) for row in cursor.fetchall()])
            except Exception as exc:  # noqa: BLE001 - the finding, not a crash
                errored += 1
                click.echo(f"EXEC-FAIL {item_id}: {str(exc)[:120]}")
                continue
            pushed_payload = payload.get("rows") or []
            pushed = canonicalize_rows(
                [
                    tuple(entry.values()) if isinstance(entry, dict) else tuple(entry)
                    for entry in pushed_payload
                ]
            )
            claimed = int(payload.get("row_count") or len(pushed))
            if claimed != len(executed):
                diverged += 1
                click.echo(
                    f"DIVERGED {item_id}: pushed row_count={claimed}, engine returned "
                    f"{len(executed)}"
                )
            elif len(pushed) == claimed and not compare_rows(
                pushed, executed, False, tolerance
            ):
                diverged += 1
                click.echo(f"DIVERGED {item_id}: pushed rows differ from engine rows")
            else:
                agreed += 1
    finally:
        engine.dispose()

    click.echo(f"checked={len(picked)} agreed={agreed} diverged={diverged} exec_failed={errored}")
