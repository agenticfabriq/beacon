"""Run mnemiq (in-process) over a small BIRD slice through beacon's harness.

Bootstraps team/solution/suite rows idempotently, ingests the BIRD
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
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import sqlalchemy as sa
from beacon_ablation.engine import AttributionEngine
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
from beacon_runner.attribution import AttributionHarnessRunner
from beacon_runner.harness import HarnessRunner
from beacon_runner.registry import SutRegistry
from beacon_runner.sut.mnemiq import MnemiqInProcessSUT, register_mnemiq_solution
from beacon_runner.types import EvalItem, SolutionConfig
from beacon_storage.db import make_engine, make_session_factory, session_scope
from beacon_storage.models.runs import HarnessMode, Run
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from uuid_extensions import uuid7

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

READ_ONLY_OPTIONS = "-c default_transaction_read_only=on"


def _per_k(entry: object, field_name: str) -> float:
    """Read one field out of a JSONB per-k attribution entry."""
    if isinstance(entry, Mapping):
        value = entry.get(field_name)
        if isinstance(value, int | float):
            return float(value)
    return 0.0


def _per_k_count(entry: object, field_name: str) -> int | None:
    """Read a COUNT out of a per-k entry, as an int or as absent.

    Separate from ``_per_k`` because that returns ``float`` and falls back to
    ``0.0``. For a count both halves are wrong: it prints ``2.0`` beside the
    integer columns, and a missing value renders as ``0.0`` -- the same value
    that means "nothing was compared". None keeps absent distinguishable from
    zero, which is the whole point of recording these.
    """
    if isinstance(entry, Mapping):
        value = entry.get(field_name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _rate(value: object) -> float:
    """Coerce a JSONB pass-rate value to float."""
    return float(value) if isinstance(value, int | float) else 0.0


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


def _run_loo_sweep(
    *,
    engine: sa.Engine,
    runner: HarnessRunner,
    sut: MnemiqInProcessSUT,
    items: list[EvalItem],
    team_id: UUID,
    suite_id: UUID,
    user_id: UUID,
    solution_record_id: UUID,
    loo_layers: list[str],
    passes: int,
) -> dict[str, object]:
    """Drive a NIGHTLY_LOO attribution sweep and summarise the persisted arms."""
    declared = {layer.name for layer in sut.layers()}
    unknown = sorted(set(loo_layers) - declared)
    if unknown:
        raise SystemExit(f"--loo names undeclared layers {unknown}; declared: {sorted(declared)}")

    factory = make_session_factory(engine)
    # Only layers listed in layers_enabled are ablated; the rest stay on
    # (SolutionConfig.is_layer_enabled defaults to True when unset).
    base_config = SolutionConfig(
        model_id=os.environ.get("MNEMIQ_LLM_MODEL", "mnemiq"),
        prompt_version="v0",
        layers_enabled=dict.fromkeys(sorted(set(loo_layers)), True),
    )
    adapter = AttributionHarnessRunner(
        runner=runner,
        session_factory=factory,
        user_id=user_id,
    )

    with session_scope(factory) as session:
        attributions = AttributionEngine(session).sweep(
            sut=sut,
            base_config=base_config,
            items=items,
            suite=SUITE,
            dataset_version=DATASET_VERSION,
            K=passes,
            suite_id=suite_id,
            team_id=team_id,
            solution_id=solution_record_id,
            harness_runner=adapter,
        )
        sweep_id = attributions[0].sweep_id
        headline = str(min(3, passes))
        layers_summary = {
            row.layer_name: {
                "delta_pass_at_k": _per_k(row.delta_pass_at_k.get(headline), "delta"),
                "ci_low": _per_k(row.delta_pass_at_k.get(headline), "ci_low"),
                "ci_high": _per_k(row.delta_pass_at_k.get(headline), "ci_high"),
                # The sample the three numbers above came over. Printing a
                # delta without it is the omission B64 is about.
                "n_compared": _per_k_count(row.delta_pass_at_k.get(headline), "n_compared"),
                "n_items_submitted": row.n_items_submitted,
                "n_baseline_excluded": row.n_baseline_excluded,
                "n_ablated_excluded": row.n_ablated_excluded,
                "mcnemar_p": row.mcnemar_p,
                "bh_adjusted_p": row.bh_adjusted_p,
                "pass_at_k_baseline": _rate(row.pass_at_k_baseline.get(headline)),
                "pass_at_k_ablated": _rate(row.pass_at_k_ablated.get(headline)),
            }
            for row in attributions
        }

    with session_scope(factory) as session:
        runs = list(session.scalars(sa.select(Run).where(Run.parent_sweep_id == sweep_id)))
        arms: dict[str, object] = {}
        for run in sorted(runs, key=lambda r: (str(r.sweep_arm), r.pass_idx)):
            results = ResultRepo(session).list_for_run(run.id)
            outcomes: dict[str, int] = {}
            for result in results:
                key = str(getattr(result.outcome, "value", result.outcome) or "none")
                outcomes[key] = outcomes.get(key, 0) + 1
            arms[f"{run.sweep_arm}#{run.pass_idx}"] = {
                "run_id": str(run.id),
                "status": str(getattr(run.status, "value", run.status)),
                "items": len(results),
                "outcomes": outcomes,
            }

    return {
        "mode": "NIGHTLY_LOO",
        "sweep_id": str(sweep_id),
        "passes": passes,
        "items": len(items),
        "ablated_layers": sorted(set(loo_layers)),
        "attribution_rows": len(attributions),
        "runs": len(runs),
        "arms": arms,
        "layers": layers_summary,
    }


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
    parser.add_argument(
        "--disable-layer",
        action="append",
        default=[],
        metavar="NAME",
        help="Declared layer to run disabled (repeatable), e.g. --disable-layer verifier.",
    )
    parser.add_argument(
        "--loo",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Run a NIGHTLY_LOO attribution sweep ablating this layer (repeatable). "
            "Produces a baseline arm plus one arm per named layer, and persists "
            "Attribution rows. Mutually exclusive with --disable-layer."
        ),
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=1,
        metavar="K",
        help="Passes per sweep arm (K) when --loo is used.",
    )
    args = parser.parse_args(argv)
    if args.loo and args.disable_layer:
        parser.error("--loo and --disable-layer are mutually exclusive")
    if args.passes < 1:
        parser.error("--passes must be >= 1")
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
            suites = SuiteRepo(session).list_for_team(team.id)
            suite_row = next((x for x in suites if x.name == args.project), None)
            if suite_row is None:
                suite_row = SuiteRepo(session).create(
                    team_id=team.id,
                    name=args.project,
                    description="",
                    method="manual",
                    suite_metadata={},
                    created_by=user_id,
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
                registry=registry,
            )
            ingest = ingest_bird_tasks(session, team_id=team.id, tasks=tasks, created_by=user_id)

            rows = EvalItemRepo(session).list_active(suite=SUITE, team_id=team.id)
            rows = [row for row in rows if row.item_input.get("db_id") == args.db_id]
            rows.sort(key=lambda row: int(row.item_metadata.get("question_id", 0)))
            rows = rows[: args.limit]

            suite_repo = SuiteRepo(session)
            suite_row = suite_repo.get_by_team_and_name(team.id, SUITE)
            if suite_row is None:
                suite_row = suite_repo.create(
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
            team_id, suite_record_id, solution_record_id = team.id, suite_row.id, solution.id

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

        if args.loo:
            print(
                json.dumps(
                    _run_loo_sweep(
                        engine=engine,
                        runner=runner,
                        sut=sut,
                        items=items,
                        team_id=team_id,
                        suite_id=suite_record_id,
                        user_id=user_id,
                        solution_record_id=solution_record_id,
                        loo_layers=args.loo,
                        passes=args.passes,
                    ),
                    indent=2,
                )
            )
            return 0

        run_id = runner.run_single(
            team_id=team_id,
            suite_id=suite_record_id,
            user_id=user_id,
            solution_record_id=solution_record_id,
            items=items,
            config=SolutionConfig(
                model_id=os.environ.get("MNEMIQ_LLM_MODEL", "mnemiq"),
                prompt_version="v0",
                layers_enabled={
                    layer.name: layer.name not in set(args.disable_layer) for layer in sut.layers()
                },
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
            outcomes: dict[str, int] = {}
            per_item: list[dict[str, object]] = []
            summary: dict[str, object] = {
                "run_id": str(run_id),
                "disabled_layers": sorted(set(args.disable_layer)),
                "status": str(run_status),
                "items": len(results),
                "outcomes": outcomes,
                "per_item": per_item,
            }
            for result in sorted(results, key=lambda r: r.item_id):
                outcome = str(getattr(result.outcome, "value", result.outcome) or "none")
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
                per_item.append(
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
