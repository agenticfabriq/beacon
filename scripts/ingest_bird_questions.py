"""Ingest BIRD Mini-Dev V2 questions into Beacon's eval_items.

Idempotent: re-running for the same team is a no-op for already-seeded
question_ids. Existing rows are reported under ``skipped``.

Usage::

    DATABASE_URL=... uv run python scripts/ingest_bird_questions.py \\
        --team acme \\
        --tasks-json .local/beacon-data/benchmarks/bird_minidev/v2-2025-07-22/\\
mini_dev_data/mini_dev_postgresql.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from beacon_benchmarks.bird_minidev.adapter import SELECTED_DBS
from beacon_benchmarks.bird_minidev.ingest_items import (
    ingest_bird_tasks,
    load_bird_tasks,
)
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_storage.db import make_engine, make_session_factory, session_scope
from beacon_storage.repository.teams import TeamRepo

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

DEFAULT_TASKS_JSON = Path(
    ".local/beacon-data/benchmarks/bird_minidev/v2-2025-07-22/mini_dev_data/mini_dev_postgresql.json"
)


def _resolve_team_id(session: Session, team_name: str) -> UUID:
    """Return the UUID for ``team_name`` or raise SystemExit."""
    team = TeamRepo(session).get_by_name(team_name)
    if team is None:
        raise SystemExit(f"team {team_name!r} not found; run `beacon demo seed` first")
    return team.id


def _resolve_seed_user_id(session: Session) -> UUID:
    """Return a stable system seed-user id for the ingest provenance trail."""
    user = UserService(session).upsert_from_oidc(
        OidcClaims(
            subject="bird-ingest-seed",
            email="bird-ingest-seed@example.com",
            name="BIRD Ingest Seed",
        )
    )
    return user.id


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tasks-json",
        type=Path,
        default=DEFAULT_TASKS_JSON,
        help=f"Path to mini_dev_postgresql.json (default: {DEFAULT_TASKS_JSON})",
    )
    parser.add_argument(
        "--team",
        required=True,
        help="Beacon team name that owns these eval items (e.g. 'acme').",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Beacon DB URL; defaults to $DATABASE_URL.",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("DATABASE_URL or --database-url is required")

    tasks = load_bird_tasks(args.tasks_json, selected_dbs=SELECTED_DBS)
    engine = make_engine(args.database_url)
    try:
        with session_scope(make_session_factory(engine)) as session:
            team_id = _resolve_team_id(session, args.team)
            seed_user_id = _resolve_seed_user_id(session)
            result = ingest_bird_tasks(
                session,
                team_id=team_id,
                tasks=tasks,
                created_by=seed_user_id,
            )
    finally:
        engine.dispose()

    print(
        json.dumps(
            {
                "team": args.team,
                "tasks_json": str(args.tasks_json),
                "inserted": result.inserted,
                "skipped": result.skipped,
                "total": result.inserted + result.skipped,
            },
            indent=2,
        )
    )
    print(f"inserted={result.inserted} skipped={result.skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
