#!/usr/bin/env python
"""What moved a published number, and what it was before.

``Result.outcome`` is overwritten in place by a regrade, so the rate you
published yesterday is not in the database today. The verdicts behind it are
-- they are append-only and versioned -- but reconstructing a rate from them
means re-deriving under the old rule and trusting that you reconstructed the
rule correctly.

``regrade_events`` records the before-value of every outcome that moved, which
makes recovery arithmetic instead: today's count, minus what this event
changed, is yesterday's count. That is what this script prints.

It answers the question "where did I get this number" and NOT "is any
published number reproducible forever". Two limits, both worth knowing before
quoting a recovered figure:

* **The run set is not bounded.** The matrix pools every valid run of a config
  identity with no time bound, so a later RUN moves a rate with no regrade
  involved at all, and nothing here sees that.
* **Recovery reverses regrades, not other writes.** ``--event`` reverses the
  selected event and every later one on that suite, which is exact for outcome
  flips. An outcome changed by anything other than a recorded regrade -- a
  re-push, a manual correction -- is invisible to it.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa
from beacon_storage.db import make_engine, make_session_factory, session_scope
from beacon_storage.models.regrade_events import RegradeEvent
from beacon_storage.models.runs import Result

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _per_run_delta(changes: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Per run, how many outcomes entered and left PASS.

    A rate's numerator is a PASS count, so the useful reading is directional:
    "+2 PASS" and "-2 PASS" are different events with the same flip count.
    """
    delta: dict[str, dict[str, int]] = defaultdict(lambda: {"gained_pass": 0, "lost_pass": 0})
    for change in changes:
        run = str(change.get("run_id", "?"))
        before, after = str(change.get("before")), str(change.get("after"))
        if after == "PASS" and before != "PASS":
            delta[run]["gained_pass"] += 1
        elif before == "PASS" and after != "PASS":
            delta[run]["lost_pass"] += 1
    return dict(delta)


def changes_from_this_event_onward(
    ordered_changes: Sequence[list[dict[str, Any]]], position: int
) -> list[list[dict[str, Any]]]:
    """The selected event's changes and every LATER event's, oldest first.

    Split out because the recovery has two halves and only the arithmetic one
    was pinned. Narrowing this to the single selected event passed every test
    while `--event <older-id>` printed exactly the never-published number the
    multi-event fix was written for -- and with the "N later regrades are
    reversed too" note suppressed, so nothing marked it.
    """
    if position < 0 or position >= len(ordered_changes):
        raise IndexError(f"event position {position} outside 0..{len(ordered_changes) - 1}")
    return list(ordered_changes[position:])


def prior_pass_counts(
    current: dict[str, int], events_from_this_one_onward: Sequence[list[dict[str, Any]]]
) -> dict[str, int]:
    """PASS per run as it stood BEFORE the oldest event in the sequence.

    Reversing ONE event against today's count is only correct when that event
    is the newest to have touched the run. With two flipping regrades on a
    suite -- which is the history this table exists to hold -- reversing just
    the selected one prints a number that was never published and nothing
    marks it as such. So every event from the selected one onward is reversed,
    which is additive and order-independent: each contributes its own net move.

    Takes the counts and the changes rather than a session, so the arithmetic
    is testable without a database. The earlier version of this recovery lived
    inline in a format string and its test recomputed the same expression, so
    inverting the sign would have left every recovered number wrong in the one
    direction nobody would question.
    """
    out = dict(current)
    for changes in events_from_this_one_onward:
        for run, delta in _per_run_delta(changes).items():
            out[run] = out.get(run, 0) - delta["gained_pass"] + delta["lost_pass"]
    return out


def _current_pass_by_run(session: Session, run_ids: list[str]) -> dict[str, int]:
    """Current PASS per run, for the run ids that are actually ids.

    `_per_run_delta` buckets a change with no `run_id` under "?" so the
    accounting cannot silently lose it. `Result.run_id` is a uuid column, so
    passing that bucket into an IN list makes the driver reject the whole
    query and the report dies instead of printing. Filtered here rather than
    at the bucket, because the bucket is the thing keeping it visible.
    """
    run_ids = [rid for rid in run_ids if _is_uuid(rid)]
    if not run_ids:
        return {}
    rows = session.execute(
        sa.select(Result.run_id, sa.func.count())
        .where(Result.run_id.in_(run_ids), Result.outcome == "PASS")
        .group_by(Result.run_id)
    ).all()
    return {str(run_id): int(n) for run_id, n in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", help="only events for this suite")
    parser.add_argument("--event", help="show the per-run before/after for one event id")
    parser.add_argument("--limit", type=int, default=20, help="how many events to list")
    parser.add_argument("--database-url", help="beacon DB URL; defaults to $DATABASE_URL")
    args = parser.parse_args()

    dsn = args.database_url or os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2

    engine = make_engine(dsn.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with session_scope(make_session_factory(engine)) as session:
            if args.event:
                event = session.get(RegradeEvent, args.event)
                if event is None:
                    print(f"no event {args.event}", file=sys.stderr)
                    return 2
                print(f"event     {event.id}")
                print(f"when      {event.created_at}")
                print(f"suite     {event.suite_name}  headline {event.headline_metric}")
                print(f"grader    {event.grader} {event.grader_version}")
                print(f"reason    {event.reason or 'none given'}")
                print(
                    f"counts    {event.n_flipped} flipped of {event.n_graded} graded, "
                    f"{event.n_already_current} already current, "
                    f"{event.n_skipped} skipped, {event.n_refused} refused"
                )
                changes = list(event.outcome_changes or [])
                delta = _per_run_delta(changes)
                if not delta:
                    print("\nNothing moved in this event.")
                    return 0

                # Every event on this suite from the selected one ONWARD, so
                # the recovered number is what stood before it rather than
                # before it minus whatever happened since.
                ordered = list(
                    session.scalars(
                        sa.select(RegradeEvent)
                        .where(RegradeEvent.suite_name == event.suite_name)
                        .order_by(RegradeEvent.created_at, RegradeEvent.id)
                    )
                )
                position = next(i for i, e in enumerate(ordered) if e.id == event.id)
                onward = changes_from_this_event_onward(
                    [list(e.outcome_changes or []) for e in ordered], position
                )
                later = len(onward) - 1

                current = _current_pass_by_run(session, list(delta))
                before_counts = prior_pass_counts(current, onward)
                if later:
                    print(
                        f"\n{later} later regrade(s) on this suite are reversed too, "
                        "so 'PASS before' is the number as it stood before THIS event."
                    )
                print("\nper run:")
                print(
                    f"  {'run':36s} {'PASS now':>9s} {'+PASS':>6s} {'-PASS':>6s} "
                    f"{'PASS before':>12s}"
                )
                for run, counts in sorted(delta.items()):
                    gained, lost = counts["gained_pass"], counts["lost_pass"]
                    # A real run absent from `current` has ZERO PASS today --
                    # the query groups over outcome == PASS, so an all-FAIL run
                    # has no row. Reading that as unknown discarded a recovery
                    # `prior_pass_counts` had computed correctly, and printed
                    # "?" where the truth was 0 and 1. Only the "?" bucket,
                    # which is a change carrying no run id, is genuinely
                    # unknown: there is no run to count.
                    known = _is_uuid(run)
                    now = current.get(run, 0) if known else None
                    before = before_counts.get(run) if known else None
                    now_s = "?" if now is None else str(now)
                    before_s = "?" if before is None else str(before)
                    print(f"  {run:36s} {now_s:>9s} {gained:>6d} {lost:>6d} {before_s:>12s}")
                total_gained = sum(c["gained_pass"] for c in delta.values())
                total_lost = sum(c["lost_pass"] for c in delta.values())
                print(f"\n  {'TOTAL':36s} {'':>9s} {total_gained:>6d} {total_lost:>6d}")
                if any(run == "?" for run in delta):
                    print(
                        "\n  NOTE: some changes carry no run_id and are bucketed as '?' -- "
                        "counted above, but no current count exists to reverse them against."
                    )
                return 0

            stmt = sa.select(RegradeEvent).order_by(RegradeEvent.created_at.desc())
            if args.suite:
                stmt = stmt.where(RegradeEvent.suite_name == args.suite)
            events = list(session.scalars(stmt.limit(args.limit)))
            if not events:
                where = f" for {args.suite}" if args.suite else ""
                print(f"no regrade events recorded{where}.")
                # Not an error: a suite that has never been regraded is the
                # normal case, and saying so beats an empty table.
                return 0
            print(f"{'when':26s} {'suite':24s} {'grader':10s} {'metric':12s} {'flipped':>7s}")
            for event in events:
                print(
                    f"{str(event.created_at)[:25]:26s} {event.suite_name[:23]:24s} "
                    f"{event.grader_version[:9]:10s} {event.headline_metric[:11]:12s} "
                    f"{event.n_flipped:>7d}   {event.reason or ''}"
                )
                print(f"{'':26s} {event.id}")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
