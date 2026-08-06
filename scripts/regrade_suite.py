"""Re-grade a suite's live results at the current ResultSetMatchGrader.

Appends verdicts at the grader's current version for every result of every
valid run in the suite (older verdict versions stay -- history, not garbage),
and re-derives ``Result.outcome`` under the suite's declared headline metric,
decision "(a)". The derivation touches ONLY results already graded PASS/FAIL:
DEFER, ERROR and TIMEOUT are the runner's statement about whether a query was
produced at all, and a regrade has no standing to change them.

Usage::

    DATABASE_URL=... uv run python scripts/regrade_suite.py --suite bird_minidev_v2
"""

from __future__ import annotations

import argparse
import os
import sys

import sqlalchemy as sa
from beacon_graders.graders.result_set_match import ResultSetMatchGrader
from beacon_runner.types import EvalItem as RunnerItem
from beacon_runner.types import ExecutionResult, ExecutionStep
from beacon_storage.db import make_engine, make_session_factory, session_scope
from beacon_storage.models.eval_items import EvalItem
from beacon_storage.models.runs import Result, Run
from beacon_storage.models.suites import Suite
from beacon_storage.repository.verdicts import VerdictRepo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="suite name, e.g. bird_minidev_v2")
    parser.add_argument("--database-url", help="beacon DB URL; defaults to $DATABASE_URL")
    args = parser.parse_args()

    dsn = args.database_url or os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2

    grader = ResultSetMatchGrader()
    engine = make_engine(dsn.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with session_scope(make_session_factory(engine)) as session:
            suite = session.scalar(sa.select(Suite).where(Suite.name == args.suite))
            if suite is None:
                print(f"no suite named {args.suite!r}", file=sys.stderr)
                return 2
            headline = suite.suite_metadata.get("headline_metric")
            if not headline:
                print(
                    f"suite {args.suite!r} declares no headline_metric; declare it "
                    "before regrading -- outcomes must derive under a stated rule",
                    file=sys.stderr,
                )
                return 2

            items = {
                str(row.item_id): row
                for row in session.scalars(
                    sa.select(EvalItem).where(
                        EvalItem.suite == suite.name, EvalItem.valid_to.is_(None)
                    )
                )
            }
            runs = list(
                session.scalars(
                    sa.select(Run).where(
                        Run.suite_id == suite.id, Run.invalidated_at.is_(None)
                    )
                )
            )

            verdict_repo = VerdictRepo(session)
            graded = skipped = flipped = orderless = 0
            true_counts: dict[str, int] = {}
            for run in runs:
                for result in session.scalars(
                    sa.select(Result).where(Result.run_id == run.id)
                ):
                    item_row = items.get(str(result.item_id))
                    if item_row is None:
                        skipped += 1
                        continue
                    # Dict-shaped rows read back from JSONB have LOST their wire
                    # column order unless an ordered columns array was stored
                    # beside them. Column order is part of exact match, so such
                    # evidence cannot be regraded -- refusing beats mangling.
                    # (Found the hard way: a regrade over orderless rows flipped
                    # 699 outcomes on scrambled columns before being reverted.)
                    stored_rows = (result.output or {}).get("rows")
                    stored_columns = (result.output or {}).get("columns")
                    if (
                        isinstance(stored_rows, list)
                        and stored_rows
                        and isinstance(stored_rows[0], dict)
                        and not stored_columns
                    ):
                        orderless += 1
                        continue
                    shim_item = RunnerItem(
                        item_id=str(item_row.item_id),
                        suite=suite.name,
                        query=dict(item_row.item_input or {}),
                        ground_truth=dict(item_row.gold_answer or {}),
                        metadata=dict(item_row.item_metadata or {}),
                    )
                    shim_result = ExecutionResult(
                        output=dict(result.output or {}),
                        output_kind=result.output_kind,
                        trace=ExecutionStep(uuid="regrade", name="regrade", level="workflow"),
                        tokens_input=0,
                        tokens_output=0,
                        runtime_ms=int(result.runtime_ms or 0),
                    )
                    if not grader.applicable(shim_item, shim_result):
                        skipped += 1
                        continue
                    verdicts = grader.grade(shim_item, shim_result)
                    by_metric = {}
                    for verdict in verdicts:
                        metric = verdict.metric or grader.metric or ""
                        by_metric[metric] = bool(verdict.bool_value)
                        verdict_repo.create(
                            team_id=result.team_id,
                            result_id=result.id,
                            grader=verdict.grader,
                            grader_version=verdict.grader_version,
                            metric=metric,
                            criterion=verdict.criterion,
                            bool_value=verdict.bool_value,
                            value=verdict.value,
                            justification=verdict.justification,
                            raw_output=verdict.raw_output,
                        )
                        if by_metric[metric]:
                            true_counts[metric] = true_counts.get(metric, 0) + 1
                    graded += 1
                    # Decision "(a)": PASS/FAIL derive from the headline verdict.
                    # Anything else is the runner's statement and stays.
                    if str(result.outcome) in {"PASS", "FAIL"}:
                        derived = "PASS" if by_metric.get(headline) else "FAIL"
                        if str(result.outcome) != derived:
                            result.outcome = derived
                            flipped += 1
            session.flush()
            print(f"suite     {suite.name}  headline {headline}")
            print(f"runs      {len(runs)} valid")
            print(
                f"graded    {graded} results at {grader.name} {grader.version}, "
                f"{skipped} skipped, {orderless} refused (no ordered columns in evidence)"
            )
            for metric in sorted(true_counts):
                print(f"  {metric:12s} {true_counts[metric]} true")
            print(f"outcomes  {flipped} flipped under the headline derivation")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
