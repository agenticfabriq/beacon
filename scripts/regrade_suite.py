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
from typing import Any

import sqlalchemy as sa
from beacon_graders.graders.result_set_match import ResultSetMatchGrader
from beacon_runner.types import EvalItem as RunnerItem
from beacon_runner.types import ExecutionResult, ExecutionStep
from beacon_storage.db import make_engine, make_session_factory, session_scope
from beacon_storage.models.eval_items import EvalItem
from beacon_storage.models.runs import Result, Run, Verdict, VerdictOutcome
from beacon_storage.models.suites import Suite
from beacon_storage.repository.verdicts import VerdictRepo


def outcome_is_the_graders_to_restate(item_input: dict[str, Any], outcome: str) -> bool:
    """Whether a regrade may re-derive this result's outcome from the headline.

    Two results are off limits. One whose outcome is not PASS/FAIL -- DEFER,
    ERROR and TIMEOUT are the runner's statement about whether a query was
    produced at all. And one whose item DECLARES itself unanswerable, where the
    refusal contract decides the outcome and the grader has no say at all
    (composer.py): refusing IS the right answer there, answering is the wrong
    one whatever came back. Re-deriving that from a result-set comparison would
    credit an over-answer whose SQL happened to match gold, and fail a refusal
    that pushed an empty row set -- inverting the one band whose whole purpose
    is detecting over-answering.
    """
    if item_input.get("answerable") is False:
        return False
    return outcome in {"PASS", "FAIL"}


class _DryRun(Exception):
    """Signal a rollback from inside ``session_scope``, which commits on return."""

    def __init__(self, flipped: int) -> None:
        super().__init__(f"dry run: {flipped} outcome(s) would flip")
        self.flipped = flipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="suite name, e.g. bird_minidev_v2")
    parser.add_argument("--database-url", help="beacon DB URL; defaults to $DATABASE_URL")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change, then roll back and write nothing",
    )
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
                    sa.select(Run).where(Run.suite_id == suite.id, Run.invalidated_at.is_(None))
                )
            )

            verdict_repo = VerdictRepo(session)
            graded = skipped = flipped = orderless = current = 0
            rederived = 0
            true_counts: dict[str, int] = {}
            for run in runs:
                for result in session.scalars(sa.select(Result).where(Result.run_id == run.id)):
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
                    #
                    # EXCEPT AT ONE COLUMN, where there is no order to lose. A
                    # single-key row has exactly one possible ordering, so the
                    # thing being protected against cannot happen. Refusing it
                    # was over-broad by nearly three quarters: of
                    # bird_minidev_v2's 9957 orderless results, 7217 (72.5%)
                    # are single-column, and none of them could be regraded --
                    # so every grader improvement skipped most of that corpus
                    # while reporting the skips only as a count.
                    #
                    # Checked across ALL rows, not just the first. A result
                    # whose rows disagree on arity is exactly the shape whose
                    # order cannot be trusted, and reading row zero alone would
                    # admit it.
                    stored_rows = (result.output or {}).get("rows")
                    stored_columns = (result.output or {}).get("columns")
                    if (
                        isinstance(stored_rows, list)
                        and stored_rows
                        and isinstance(stored_rows[0], dict)
                        and not stored_columns
                        and max(
                            (len(row) for row in stored_rows if isinstance(row, dict)),
                            default=0,
                        )
                        > 1
                    ):
                        orderless += 1
                        continue
                    # One verdict per (result, metric, grader, version) -- the
                    # schema enforces it, and a re-run must be a no-op, not a
                    # crash into the unique index.
                    already = session.scalar(
                        sa.select(sa.func.count())
                        .select_from(Verdict)
                        .where(
                            Verdict.result_id == result.id,
                            Verdict.grader == grader.name,
                            Verdict.grader_version == grader.version,
                        )
                    )
                    if already:
                        current += 1
                        # A CURRENT VERDICT IS NOT A CURRENT OUTCOME. The
                        # outcome derives from the suite's headline metric,
                        # which changes independently of the grader version --
                        # and did: spider2_lite_local_v1 declares got_facts
                        # while the ingest path composed under exact_match
                        # until it was taught to read the declaration (B74).
                        #
                        # Returning here left 90 results on three live runs
                        # carrying an outcome derived under the wrong metric,
                        # and INVISIBLY, because they counted as `current` and
                        # the summary said "3 flipped" -- 3 of 93. A green run
                        # certifying stale data is worse than no run.
                        #
                        # The grade is still skipped: the unique index forbids
                        # a duplicate, and re-grading identical inputs at the
                        # same version yields an identical verdict. The
                        # DERIVATION is not skipped -- it reads the verdict
                        # already stored for the headline metric.
                        if not outcome_is_the_graders_to_restate(
                            dict(item_row.item_input or {}), str(result.outcome)
                        ):
                            continue
                        stored = session.scalar(
                            sa.select(Verdict).where(
                                Verdict.result_id == result.id,
                                Verdict.grader == grader.name,
                                Verdict.grader_version == grader.version,
                                Verdict.metric == headline,
                            )
                        )
                        if stored is None or stored.bool_value is None:
                            continue
                        derived = VerdictOutcome.PASS if stored.bool_value else VerdictOutcome.FAIL
                        if str(result.outcome) != derived.value:
                            result.outcome = derived
                            flipped += 1
                            rederived += 1
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
                        # This shim is only ever handed to the grader -- the
                        # regrade writes verdicts and `outcome`, never a result
                        # row -- so it should show the grader the attempt as it
                        # was recorded rather than a cost of zero the attempt
                        # never reported.
                        tokens_input=result.tokens_input,
                        tokens_output=result.tokens_output,
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
                    # Verdicts above are recorded as evidence either way; only the
                    # outcome is not always the grader's to restate.
                    if outcome_is_the_graders_to_restate(
                        dict(item_row.item_input or {}), str(result.outcome)
                    ):
                        derived = (
                            VerdictOutcome.PASS if by_metric.get(headline) else VerdictOutcome.FAIL
                        )
                        if str(result.outcome) != derived.value:
                            result.outcome = derived
                            flipped += 1
            session.flush()
            print(f"suite     {suite.name}  headline {headline}")
            print(f"runs      {len(runs)} valid")
            print(
                f"graded    {graded} results at {grader.name} {grader.version}, "
                f"{current} already at this version, {skipped} skipped, "
                f"{orderless} refused (no ordered columns in evidence)"
            )
            for metric in sorted(true_counts):
                print(f"  {metric:12s} {true_counts[metric]} true")
            print(
                f"outcomes  {flipped} flipped under the headline derivation "
                f"({rederived} of them from a verdict already at this version, "
                f"where only the derivation was stale)"
            )
            if args.dry_run:
                # Raised INSIDE session_scope, whose except branch rolls back.
                # Returning early would commit: the scope commits on success,
                # so a dry run has to leave by the failure path.
                #
                # This exists because a flip is the one irreversible part.
                # Verdicts are APPENDED -- older versions stay, history not
                # garbage -- but `result.outcome` is overwritten in place with
                # no record of what it was, and the count is printed only
                # after the flush. On a corpus a peer has already published
                # numbers from, "run it and read the summary" means finding out
                # too late.
                raise _DryRun(flipped)
    except _DryRun as signal:
        print(f"\nDRY RUN -- nothing written. {signal.flipped} outcome(s) would flip.")
        return 0
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
