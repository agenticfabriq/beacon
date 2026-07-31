"""Run mnemiq (in-process) over a small BIRD slice through beacon's harness.

Bootstraps team/project/solution/suite rows idempotently, ingests the BIRD
questions for one database, runs ``MnemiqInProcessSUT`` through
``HarnessRunner`` with the execution-grounded SQL grader, and prints a
per-item summary of the persisted run.

Requires:

- mnemiq installed in this environment: ``uv pip install -e ../mnemiq``
  (re-run after an explicit ``uv sync``, which prunes it)
- mnemiq's LLM env exported (``MNEMIQ_LLM_*``; embeddings fall back to the
  chat endpoint)
- the BIRD mini-dev Postgres database (one flat db holding all mini-dev
  tables, e.g. ``bird_dev``)
- beacon's own Postgres migrated (``make db-up && make migrate``)

Usage::

    DATABASE_URL=... uv run python scripts/run_mnemiq_bird_slice.py \\
        --minidev-dir "$MNEMIQ_MINIDEV_DIR" \\
        --bird-dsn postgresql://user:pass@localhost:5433/bird_dev \\
        --db-id california_schools --limit 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import sqlalchemy as sa
from beacon_benchmarks.bird_minidev.ingest_items import (
    DATASET_VERSION,
    SUITE,
    ingest_bird_tasks,
    load_bird_tasks,
)
from beacon_graders.composer import VerdictComposer
from beacon_graders.graders import ExecutionGroundedSqlGrader
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_runner.harness import HarnessRunner
from beacon_runner.registry import SutRegistry
from beacon_runner.sut.mnemiq import MnemiqInProcessSUT, register_mnemiq_solution
from beacon_runner.types import EvalItem, SolutionConfig
from beacon_storage.db import make_engine, make_session_factory, session_scope
from beacon_storage.models.runs import HarnessMode
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from uuid_extensions import uuid7

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

READ_ONLY_OPTIONS = "-c default_transaction_read_only=on"


def _read_only_dsn(dsn: str) -> str:
    """Append a read-only session option to a libpq DSN URL."""
    separator = "&" if "?" in dsn else "?"
    # libpq needs the space AND the inner "=" percent-encoded inside the value.
    return f"{dsn}{separator}options={quote(READ_ONLY_OPTIONS, safe='')}"


def _sqlalchemy_url(dsn: str) -> str:
    return dsn.replace("postgresql://", "postgresql+psycopg://", 1)


def _seed_user_id(session: Session) -> UUID:
    user = UserService(session).upsert_from_oidc(
        OidcClaims(
            subject="mnemiq-slice-seed",
            email="mnemiq-slice-seed@example.com",
            name="mnemiq Slice Seed",
        )
    )
    return user.id


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Beacon DB URL; defaults to $DATABASE_URL.",
    )
    parser.add_argument(
        "--minidev-dir",
        default=os.environ.get("MNEMIQ_MINIDEV_DIR"),
        help="Extracted BIRD MINIDEV directory; defaults to $MNEMIQ_MINIDEV_DIR.",
    )
    parser.add_argument("--bird-dsn", required=True, help="BIRD mini-dev Postgres DSN.")
    parser.add_argument("--db-id", default="california_schools")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--team", default="mnemiq")
    parser.add_argument("--project", default="mnemiq-bird")
    parser.add_argument("--enrich-cache", default=".local/mnemiq-enrich-cache")
    parser.add_argument("--candidates", type=int, default=3)
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("DATABASE_URL or --database-url is required")
    if not args.minidev_dir:
        parser.error("MNEMIQ_MINIDEV_DIR or --minidev-dir is required")

    tasks_json = Path(args.minidev_dir) / "mini_dev_postgresql.json"
    tasks = load_bird_tasks(tasks_json, selected_dbs=(args.db_id,))[: args.limit]
    if not tasks:
        parser.error(f"no BIRD tasks found for db_id={args.db_id!r} in {tasks_json}")

    engine = make_engine(args.database_url)
    registry = SutRegistry()

    try:
        with session_scope(make_session_factory(engine)) as session:
            user_id = _seed_user_id(session)
            team = TeamRepo(session).get_by_name(args.team)
            if team is None:
                team = TeamRepo(session).create(name=args.team)
            projects = ProjectRepo(session).list_for_team(team.id)
            project = next((p for p in projects if p.name == args.project), None)
            if project is None:
                project = ProjectRepo(session).create(
                    team_id=team.id, name=args.project, created_by=user_id
                )
            sut = MnemiqInProcessSUT(
                owner_team_id=team.id,
                minidev_dir=args.minidev_dir,
                bird_dsn=_read_only_dsn(args.bird_dsn),
                enrich_cache_dir=args.enrich_cache,
                candidates=args.candidates,
            )
            solution = register_mnemiq_solution(
                session,
                team_id=team.id,
                created_by=user_id,
                sut=sut,
                project_id=project.id,
                registry=registry,
            )
            ingest = ingest_bird_tasks(session, team_id=team.id, tasks=tasks, created_by=user_id)

            rows = EvalItemRepo(session).list_active(suite=SUITE, team_id=team.id)
            rows = [row for row in rows if row.item_input.get("db_id") == args.db_id]
            rows.sort(key=lambda row: int(row.item_metadata.get("question_id", 0)))
            rows = rows[: args.limit]

            suite_repo = SuiteRepo(session)
            suite_row = suite_repo.get_by_project_and_name(project.id, SUITE)
            if suite_row is None:
                suite_row = suite_repo.create(
                    project_id=project.id,
                    team_id=team.id,
                    name=SUITE,
                    description=f"mnemiq BIRD slice ({args.db_id} x {args.limit})",
                    method="handpicked",
                    suite_metadata={"db_id": args.db_id, "limit": args.limit},
                    created_by=user_id,
                )
            suite_repo.add_items(suite_id=suite_row.id, item_ids=[row.item_id for row in rows])

            items = [
                EvalItem(
                    item_id=str(row.item_id),
                    suite=row.suite,
                    query=row.item_input,
                    ground_truth=row.gold_answer,
                    metadata=row.item_metadata,
                )
                for row in rows
            ]
            team_id, project_id, solution_record_id = team.id, project.id, solution.id

        print(f"ingest: inserted={ingest.inserted} skipped={ingest.skipped}; slice={len(items)}")

        bird_engine = sa.create_engine(
            _sqlalchemy_url(args.bird_dsn),
            connect_args={"options": READ_ONLY_OPTIONS},
            pool_pre_ping=True,
        )
        composer = VerdictComposer(
            graders=[ExecutionGroundedSqlGrader(engine_factory=lambda _item: bird_engine)]
        )
        runner = HarnessRunner(
            session_factory=make_session_factory(engine),
            registry=registry,
            composer=composer,
            max_workers=1,
            per_item_timeout_seconds=600.0,
        )
        run_id = runner.run_single(
            team_id=team_id,
            project_id=project_id,
            user_id=user_id,
            solution_record_id=solution_record_id,
            items=items,
            config=SolutionConfig(
                model_id=os.environ.get("MNEMIQ_LLM_MODEL", "mnemiq"),
                prompt_version="v0",
                layers_enabled={layer.name: True for layer in sut.layers()},
            ),
            suite=SUITE,
            dataset_version=DATASET_VERSION,
            pass_idx=0,
            mode=HarnessMode.EVAL,
            parent_sweep_id=uuid7(),
        )

        with session_scope(make_session_factory(engine)) as session:
            run = RunRepo(session).get(run_id)
            run_status = getattr(run.status, "value", run.status) if run else "unknown"
            results = ResultRepo(session).list_for_run(run_id)
            summary = {
                "run_id": str(run_id),
                "status": str(run_status),
                "items": len(results),
                "outcomes": {},
                "per_item": [],
            }
            for result in sorted(results, key=lambda r: r.item_id):
                outcome = str(getattr(result.outcome, "value", result.outcome) or "none")
                summary["outcomes"][outcome] = summary["outcomes"].get(outcome, 0) + 1
                summary["per_item"].append(
                    {
                        "item_id": result.item_id,
                        "outcome": outcome,
                        "deferred": bool(result.output.get("deferred", False)),
                        "tokens": result.tokens_output,
                        "runtime_ms": result.runtime_ms,
                    }
                )
        print(json.dumps(summary, indent=2))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
