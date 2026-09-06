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
from beacon_graders.graders.result_set_match import ResultSetMatchGrader, explicit_columns
from beacon_runner.types import EvalItem as RunnerItem
from beacon_runner.types import ExecutionResult, ExecutionStep
from beacon_storage.db import make_engine, make_session_factory, session_scope
from beacon_storage.models.eval_items import EvalItem
from beacon_storage.models.regrade_events import RegradeEvent
from beacon_storage.models.runs import Result, Run, Verdict, VerdictOutcome
from beacon_storage.models.suites import Suite
from beacon_storage.repository.outcome_history import OutcomeHistoryRepo
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


def rows_have_unrecoverable_column_order(output: dict[str, Any]) -> bool:
    """Whether this result's rows lost an order that mattered.

    Dict-shaped rows read back from JSONB have lost their wire column order
    unless an ordered ``columns`` array was stored beside them. Column order is
    part of exact match, so such evidence cannot be regraded -- refusing beats
    mangling, learned the hard way when a regrade over orderless rows flipped
    699 outcomes on scrambled columns.

    EXCEPT AT ONE COLUMN, where there is no order to lose: a single-key row has
    exactly one possible ordering, so the hazard cannot occur. Refusing those
    was over-broad by nearly three quarters -- of bird_minidev_v2's 9957
    orderless results, 7217 (72.5%) are single-column, and none could be
    regraded, so every grader improvement skipped most of that corpus.

    "Carries a columns array" is asked through the grader's own
    ``explicit_columns``, not by truthiness. A truthy-but-unusable value like
    ``[0, 1]`` answers None there, so a truthiness test clears this gate while
    the grader falls back to insertion order -- admitting exactly the scrambled
    evidence the refusal exists to keep out.

    Arity is read across EVERY row. A result whose rows disagree on arity is
    exactly the shape whose order cannot be trusted, and reading row zero alone
    would admit it.

    A module-level function rather than an expression inside ``main`` so it can
    be tested against row payloads. As an inline predicate the only available
    test was structural -- it asserted the expression mentioned ``len`` and a
    generator, which `min` for `max`, `stored_rows[:1]` for `stored_rows`, and
    `> 0` for `> 1` all satisfy while breaking exactly what it claimed to pin.
    """
    rows = output.get("rows")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return False
    if explicit_columns(output.get("columns")) is not None:
        return False
    widest = max((len(row) for row in rows if isinstance(row, dict)), default=0)
    return widest > 1


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
    parser.add_argument(
        "--reason",
        help="why this regrade is being run, recorded on the event "
        "(e.g. 'B74 headline wiring'). Free text: the useful version is "
        "the one no enum would have anticipated.",
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
            history = OutcomeHistoryRepo(session)
            graded = skipped = flipped = orderless = current = 0
            rederived = 0
            # One entry per outcome that MOVES, with the value it moved
            # from. That before-value is what turns recovery from a
            # re-derivation into arithmetic.
            changes: list[dict[str, Any]] = []
            true_counts: dict[str, int] = {}
            for run in runs:
                for result in session.scalars(sa.select(Result).where(Result.run_id == run.id)):
                    item_row = items.get(str(result.item_id))
                    if item_row is None:
                        skipped += 1
                        continue
                    if rows_have_unrecoverable_column_order(dict(result.output or {})):
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
                        # Recorded for EVERY result this regrade re-derived, not
                        # only the ones that moved. `record` already skips an
                        # unchanged outcome under an unchanged derivation, so
                        # the on-change rule still holds -- but a result whose
                        # outcome stayed while the DERIVATION changed has to be
                        # re-attributed, or its history keeps crediting the
                        # previous rule. Recording only flips left 7,449 of the
                        # bird regrade's 7,747 attributed to the old derivation,
                        # so a read half filtering by derivation_id would have
                        # computed over 298 of 7,747: the partial denominator
                        # this whole table exists to prevent.
                        history.record(
                            team_id=result.team_id,
                            result_id=result.id,
                            outcome=derived.value,
                            source="regrade",
                            grader=grader.name,
                            grader_version=grader.version,
                            metric=headline,
                        )
                        if str(result.outcome) != derived.value:
                            changes.append(
                                {
                                    "result_id": str(result.id),
                                    "run_id": str(run.id),
                                    "item_id": str(result.item_id),
                                    "before": str(result.outcome),
                                    "after": derived.value,
                                    "source": "rederived_from_stored_verdict",
                                }
                            )
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
                        # Same reason as the already-current branch above: every
                        # re-derivation is attributed, not only the flips, or a
                        # derivation filter reads a partial denominator.
                        history.record(
                            team_id=result.team_id,
                            result_id=result.id,
                            outcome=derived.value,
                            source="regrade",
                            grader=grader.name,
                            grader_version=grader.version,
                            metric=headline,
                        )
                        if str(result.outcome) != derived.value:
                            changes.append(
                                {
                                    "result_id": str(result.id),
                                    "run_id": str(run.id),
                                    "item_id": str(result.item_id),
                                    "before": str(result.outcome),
                                    "after": derived.value,
                                    "source": "graded_at_this_version",
                                }
                            )
                            result.outcome = derived
                            flipped += 1
            session.flush()
            print(f"suite     {suite.name}  headline {headline}")
            print(f"runs      {len(runs)} valid")
            print(
                f"graded    {graded} results at {grader.name} {grader.version}, "
                f"{current} already at this version, {skipped} skipped, "
                f"{orderless} refused (multi-column rows with no ordered columns "
                f"in evidence; single-column rows are regraded, having no "
                f"order to lose)"
            )
            for metric in sorted(true_counts):
                print(f"  {metric:12s} {true_counts[metric]} true")
            print(
                f"outcomes  {flipped} flipped under the headline derivation "
                f"({rederived} of them from a verdict already at this version, "
                f"where only the derivation was stale)"
            )
            # The record goes in the SAME transaction as the outcomes it
            # describes, so an event exists exactly when the change did. On a
            # dry run the scope rolls back and takes this with it, which is
            # right: nothing moved, so there is nothing to explain.
            #
            # Written even when nothing flipped. "I ran the regrade and it
            # changed nothing" is a fact worth having -- without it, silence
            # is indistinguishable from never having run.
            event = RegradeEvent(
                # Snapshotted from the suite, which is where 0024's backfill
                # took it from. Not optional: the column is NOT NULL and the
                # RLS policy is built on it, so a writer that omits it fails
                # loudly here rather than producing a row nobody can read.
                team_id=suite.team_id,
                suite_id=suite.id,
                suite_name=suite.name,
                grader=grader.name,
                grader_version=grader.version,
                headline_metric=headline,
                n_runs=len(runs),
                n_graded=graded,
                n_already_current=current,
                n_skipped=skipped,
                n_refused=orderless,
                n_flipped=flipped,
                outcome_changes=changes,
                reason=args.reason,
            )
            session.add(event)
            session.flush()
            if not args.dry_run:
                print(f"recorded  event {event.id}  (reason: {args.reason or 'none given'})")
                print(
                    "          look it up later with: DATABASE_URL=... uv run "
                    f"python scripts/regrade_history.py --suite {suite.name}"
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
