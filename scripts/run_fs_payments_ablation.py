"""Register the fs payments experiment and run the certified-records ablation.

The corpus exists to answer one question -- does Verity's certified semantic
layer change mnemiq's answers -- and this script is the one command that
answers it. It bootstraps team/suite/solution idempotently, ingests the
measured gold (versioned re-ingest on change), REFUSES to run unless gold and
execution corpus are provably the same bytes, and drives the two-arm
NIGHTLY_LOO sweep (baseline vs ``no_certified_records``) through the harness,
with paired McNemar + bootstrap + BH stats persisted as Attribution rows.

The digest gate is the point, not a flourish: the items carry the sha256 of
the corpus their gold was MEASURED against at ingest; the sweep executes
against the corpus file on disk. Those are two different facts, and when they
disagree -- a regenerated corpus nobody re-ingested -- the run would compare
answers from one dataset against gold from another and report noise with a
confidence interval. Refusing beats that.

Requires mnemiq installed (``uv pip install -e ../mnemiq``), mnemiq's LLM env
exported, the exported Verity records snapshot, and beacon's Postgres
migrated.

Usage::

    DATABASE_URL=... uv run python scripts/run_fs_payments_ablation.py \\
        --gold ~/src/fabriq/mnemiq-internal/corpus/fs_payments/gold.json \\
        --database ~/src/dataset/fs_payments/fs_payments.duckdb \\
        --records-url file:///path/to/records-export.json \\
        --expect-records 38

``--register-only`` stops after ingest + registration + the digest gate, so
the sweep (which spends LLM budget) stays a deliberate second step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
from beacon_ablation.engine import AttributionEngine
from beacon_benchmarks.fs_payments import (
    DATASET_VERSION,
    HEADLINE_METRIC,
    SUITE,
    ingest_fs_payments_tasks,
)
from beacon_graders.composer import VerdictComposer
from beacon_graders.graders import ResultSetMatchGrader
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_runner.attribution import AttributionHarnessRunner
from beacon_runner.harness import HarnessRunner
from beacon_runner.registry import SutRegistry
from beacon_runner.sut.mnemiq import MnemiqFsPaymentsSUT, register_mnemiq_solution
from beacon_runner.types import EvalItem, SolutionConfig
from beacon_storage.db import make_engine, make_session_factory, session_scope
from beacon_storage.models.runs import Run
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo

if TYPE_CHECKING:
    from uuid import UUID

    # Aliased: `EvalItem` is already the RUNNER's item here (the thing a SUT is
    # asked), and this is the STORED row (the thing gold lives on). Two types,
    # one name, one script -- the annotation has to say which.
    from beacon_storage.models.eval_items import EvalItem as StoredEvalItem
    from sqlalchemy.orm import Session


def _seed_user_id(session: Session) -> UUID:
    user = UserService(session).upsert_from_oidc(
        OidcClaims(
            subject="fs-payments-seed",
            email="fs-payments-seed@example.com",
            name="fs payments seed",
        )
    )
    return user.id


def _digest_gate(rows: list[StoredEvalItem], database_path: Path) -> str:
    """Gold and execution must come from the same corpus bytes, provably.

    The items' ``corpus_sha256`` records what the gold was measured against;
    the file digest records what the sweep would execute against. Two
    measurements that can disagree are an instrument -- this is where it reads.
    """
    item_digests = {str((row.item_metadata or {}).get("corpus_sha256")) for row in rows}
    if len(item_digests) != 1:
        raise SystemExit(
            f"items carry {len(item_digests)} distinct corpus digests; the suite mixes "
            "ingests from different corpus states -- re-ingest before running"
        )
    (items_digest,) = item_digests
    db_digest = hashlib.sha256(database_path.read_bytes()).hexdigest()
    if items_digest != db_digest:
        raise SystemExit(
            f"corpus digest mismatch: gold was measured against {items_digest[:12]}..., "
            f"execution would run against {db_digest[:12]}... Re-ingest after "
            "regenerating, or point --database at the corpus the gold was measured "
            "on. A run whose gold and engine disagree about the data measures nothing."
        )
    return db_digest


def _per_k(entry: object, field_name: str) -> float:
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
    return float(value) if isinstance(value, int | float) else 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Beacon DB URL; defaults to $DATABASE_URL.",
    )
    parser.add_argument("--gold", required=True, help="fs payments gold.json")
    parser.add_argument("--database", required=True, help="fs_payments.duckdb corpus file")
    parser.add_argument(
        "--records-url",
        required=True,
        help="Verity records snapshot (file://...) or endpoint for the grounded arm",
    )
    parser.add_argument(
        "--expect-records",
        type=int,
        required=True,
        help="certified record count the grounded arm must fetch; fewer refuses the arm",
    )
    parser.add_argument("--team", default="fspay")
    parser.add_argument("--enrich-cache", default=".local/fs-payments-enrich-cache")
    parser.add_argument("--candidates", type=int, default=3)
    parser.add_argument("--passes", type=int, default=1, metavar="K")
    parser.add_argument(
        "--register-only",
        action="store_true",
        help="ingest, register and gate, then stop -- the sweep stays a deliberate step",
    )
    args = parser.parse_args(argv)
    if args.passes < 1:
        parser.error("--passes must be >= 1")
    if not args.database_url:
        parser.error("DATABASE_URL or --database-url is required")

    gold_path = Path(args.gold).expanduser()
    database_path = Path(args.database).expanduser()
    if not gold_path.is_file():
        parser.error(f"no gold file: {gold_path}")
    if not database_path.is_file():
        parser.error(f"no corpus database: {database_path}")

    engine = make_engine(args.database_url)
    registry = SutRegistry()

    try:
        with session_scope(make_session_factory(engine)) as session:
            user_id = _seed_user_id(session)
            team = TeamRepo(session).get_by_name(args.team)
            if team is None:
                team = TeamRepo(session).create(name=args.team)

            ingest = ingest_fs_payments_tasks(
                gold_path=gold_path,
                database_path=database_path,
                items_repo=EvalItemRepo(session),
                team_id=team.id,
                created_by=user_id,
            )

            suite_repo = SuiteRepo(session)
            suite_row = suite_repo.get_by_team_and_name(team.id, SUITE)
            if suite_row is None:
                suite_row = suite_repo.create(
                    team_id=team.id,
                    name=SUITE,
                    description=(
                        "fs payments: 29 questions in three bands over a 20k-payment "
                        "DuckDB corpus with five proven traps. Exists to measure whether "
                        "Verity's certified records change mnemiq's answers."
                    ),
                    method="manual",
                    suite_metadata={
                        "source": "fs-payments-local",
                        # The one home of the declaration; items carry no copy.
                        "headline_metric": HEADLINE_METRIC,
                    },
                    created_by=user_id,
                )
            if suite_row.suite_metadata.get("headline_metric") != HEADLINE_METRIC:
                suite_row.suite_metadata = {
                    **suite_row.suite_metadata,
                    "headline_metric": HEADLINE_METRIC,
                }
                session.flush()

            rows = EvalItemRepo(session).list_active(suite=SUITE, team_id=team.id)
            rows.sort(key=lambda row: str(row.item_metadata.get("case_id", "")))
            suite_repo.add_items(suite_id=suite_row.id, item_ids=[row.item_id for row in rows])

            corpus_digest = _digest_gate(rows, database_path)

            sut = MnemiqFsPaymentsSUT(
                owner_team_id=team.id,
                database_path=str(database_path),
                records_url=args.records_url,
                enrich_cache_dir=args.enrich_cache,
                expect_records=args.expect_records,
                candidates=args.candidates,
            )
            solution = register_mnemiq_solution(
                session,
                team_id=team.id,
                created_by=user_id,
                sut=sut,
                registry=registry,
            )

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

        print(
            f"suite     {SUITE} ({DATASET_VERSION})  headline {HEADLINE_METRIC}\n"
            f"items     {ingest.inserted} inserted, {ingest.refreshed} re-versioned, "
            f"{ingest.skipped} already current; {len(items)} in the sweep\n"
            f"corpus    {corpus_digest[:12]}... (gold and execution provably the same bytes)\n"
            f"records   {args.records_url} (expect {args.expect_records})"
        )
        if args.register_only:
            print("register-only: stopping before the sweep, as asked")
            return 0

        # Answer-authority grading, same grader as every benchmark: the SUT
        # pushes the rows its engine returned, the composer compares against
        # the materialized gold. Deferrals and errors score before any grader.
        composer = VerdictComposer(graders=[ResultSetMatchGrader()])
        runner = HarnessRunner(
            session_factory=make_session_factory(engine),
            registry=registry,
            composer=composer,
            max_workers=1,
            per_item_timeout_seconds=600.0,
        )
        factory = make_session_factory(engine)
        base_config = SolutionConfig(
            model_id=os.environ.get("MNEMIQ_LLM_MODEL", "mnemiq"),
            prompt_version="v0",
            # Only the experiment's layer is enumerated: baseline plus
            # no_certified_records, the two arms and nothing else.
            layers_enabled={"certified_records": True},
        )
        adapter = AttributionHarnessRunner(runner=runner, session_factory=factory, user_id=user_id)

        with session_scope(factory) as session:
            attributions = AttributionEngine(session).sweep(
                sut=sut,
                base_config=base_config,
                items=items,
                suite=SUITE,
                dataset_version=DATASET_VERSION,
                K=args.passes,
                suite_id=suite_record_id,
                team_id=team_id,
                solution_id=solution_record_id,
                harness_runner=adapter,
            )
            sweep_id = attributions[0].sweep_id
            headline_k = str(min(3, args.passes))
            layers_summary = {
                row.layer_name: {
                    "delta_pass_at_k": _per_k(row.delta_pass_at_k.get(headline_k), "delta"),
                    "ci_low": _per_k(row.delta_pass_at_k.get(headline_k), "ci_low"),
                    "ci_high": _per_k(row.delta_pass_at_k.get(headline_k), "ci_high"),
                    # The sample the three numbers above came over. Printing a
                    # delta without it is the omission B64 is about.
                    "n_compared": _per_k_count(row.delta_pass_at_k.get(headline_k), "n_compared"),
                    "n_items_submitted": row.n_items_submitted,
                    "n_baseline_excluded": row.n_baseline_excluded,
                    "n_ablated_excluded": row.n_ablated_excluded,
                    "mcnemar_p": row.mcnemar_p,
                    "bh_adjusted_p": row.bh_adjusted_p,
                    "pass_at_k_baseline": _rate(row.pass_at_k_baseline.get(headline_k)),
                    "pass_at_k_ablated": _rate(row.pass_at_k_ablated.get(headline_k)),
                }
                for row in attributions
            }

        with session_scope(factory) as session:
            runs = list(session.scalars(sa.select(Run).where(Run.parent_sweep_id == sweep_id)))
            arms: dict[str, object] = {}
            gradeable = 0
            for run in sorted(runs, key=lambda r: (str(r.sweep_arm), r.pass_idx)):
                results = ResultRepo(session).list_for_run(run.id)
                outcomes: dict[str, int] = {}
                for result in results:
                    key = str(getattr(result.outcome, "value", result.outcome) or "none")
                    outcomes[key] = outcomes.get(key, 0) + 1
                    if key in ("PASS", "FAIL"):
                        gradeable += 1
                arms[f"{run.sweep_arm}#{run.pass_idx}"] = {
                    "run_id": str(run.id),
                    "status": str(getattr(run.status, "value", run.status)),
                    "items": len(results),
                    "outcomes": outcomes,
                }
        if gradeable == 0:
            # A sweep in which NOTHING was gradeable is an instrument reading,
            # not an effect: delta 0.0 with p 1.0 over zero passes is precisely
            # the manufactured null this corpus was built to make impossible.
            # (Learned live: the first sweep scored ERROR on every non-deferred
            # item because the SUT pushed no rows and the grader never applied.)
            print(json.dumps({"void": True, "arms": arms}, indent=2))
            raise SystemExit(
                "VOID sweep: zero gradeable results in every arm -- the grader never "
                "applied. Did the SUT push rows? Runs are persisted for diagnosis; "
                "invalidate them once diagnosed. Refusing to print statistics."
            )

        print(
            json.dumps(
                {
                    "mode": "NIGHTLY_LOO",
                    "sweep_id": str(sweep_id),
                    "passes": args.passes,
                    "items": len(items),
                    "corpus_sha256": corpus_digest,
                    "arms": arms,
                    "layers": layers_summary,
                },
                indent=2,
            )
        )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
