"""GET /v1/suites/{suite_id}/history -- what moved this benchmark's numbers.

``Result.outcome`` is overwritten in place by a regrade, so before
``regrade_events`` existed a published rate could move with the reason living
only in a terminal someone had already closed. These two endpoints are the read
side of that record.

They deliberately do NOT rewind anything. Every number the matrix serves is
still the current one; this says what changed and when, which is the cheap half
and alters no existing figure.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID  # noqa: TC003

import sqlalchemy as sa
from beacon_iam.permissions import Permission
from beacon_storage.models.runs import Result, Run
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.regrade_events import RegradeEventRepo
from beacon_storage.repository.suites import SuiteRepo
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.regrade_event import (
    RegradeEventDetailOut,
    RegradeEventOut,
    RegradeEventRunOut,
)

if TYPE_CHECKING:
    from beacon_storage.models.regrade_events import RegradeEvent

router = APIRouter(prefix="/v1", tags=["history"])


def _event_out(event: RegradeEvent) -> RegradeEventOut:
    return RegradeEventOut(
        id=event.id,
        recorded_at=event.created_at,
        grader=event.grader,
        grader_version=event.grader_version,
        headline_metric=event.headline_metric,
        n_runs=event.n_runs,
        n_graded=event.n_graded,
        n_flipped=event.n_flipped,
        reason=event.reason,
    )


def _uuid_or_none(value: Any) -> UUID | None:
    """A UUID from a JSONB string, or None if it is not one.

    ``outcome_changes`` is JSONB written by a script, so its ids are strings
    and nothing at the database level constrains them. A malformed entry drops
    out of the breakdown instead of raising: the event's own counters are the
    authority on how much moved, and one unparseable id should not take the
    whole page down with it.
    """
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


@router.get(
    "/suites/{suite_id}/history",
    response_model=list[RegradeEventOut],
    summary="List recorded regrades for a benchmark, newest first",
)
@requires(Permission.EVAL_VIEW)
def list_suite_history(
    suite_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_VIEW, scope_kind="suite")),
    ],
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[RegradeEventOut]:
    """Return this benchmark's recorded regrades.

    An empty list means "nothing recorded", which is NOT the same claim as
    "nothing changed" -- recording began when 0022 was applied, and any
    correction before that is unrecoverable. The distinction belongs to the
    caller to render, and the UI is required to make it, because the two are
    opposite facts that otherwise look identical.
    """
    if SuiteRepo(session).get(suite_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "suite not found")
    events = RegradeEventRepo(session).list_for_suite(suite_id=suite_id, limit=limit)
    return [_event_out(event) for event in events]


@router.get(
    "/suites/{suite_id}/history/{event_id}",
    response_model=RegradeEventDetailOut,
    summary="One recorded regrade, with its per-run breakdown",
)
@requires(Permission.EVAL_VIEW)
def get_suite_history_event(
    suite_id: UUID,
    event_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_VIEW, scope_kind="suite")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> RegradeEventDetailOut:
    """Return one event, plus what each affected run's PASS count did."""
    if SuiteRepo(session).get(suite_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "suite not found")
    event = RegradeEventRepo(session).get_for_suite(suite_id=suite_id, event_id=event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found for this benchmark")

    # Gains and losses per run, from the event's own recorded changes.
    #
    # Counted against PASS specifically rather than "before != after", because
    # the PASS count is what the run's rate is built from: a DEFER -> FAIL move
    # is a real change that leaves the numerator alone, and folding it into
    # `gained`/`lost` would make `pass_before` disagree with arithmetic a
    # reader can do on screen.
    moves: dict[UUID, dict[str, int]] = defaultdict(lambda: {"gained": 0, "lost": 0})
    transitions: Counter[str] = Counter()
    for change in event.outcome_changes:
        if not isinstance(change, dict):
            continue
        before, after = str(change.get("before")), str(change.get("after"))
        transitions[f"{before}->{after}"] += 1
        run_id = _uuid_or_none(change.get("run_id"))
        if run_id is None:
            continue
        # Register the run on ANY recorded change, before deciding whether the
        # change crossed PASS. A DEFER -> FAIL move leaves both counters at
        # zero, and a run whose only change was that one is still a run this
        # event touched: gating entry on the counters reported it as
        # unaffected, which is a measurement of nothing presented as nothing
        # happening. Caught by the DEFER -> FAIL test, which is why that test
        # asserts `runs_affected` and not only the counters.
        move = moves[run_id]
        if after == "PASS" and before != "PASS":
            move["gained"] += 1
        elif before == "PASS" and after != "PASS":
            move["lost"] += 1

    if not moves:
        return RegradeEventDetailOut(
            event=_event_out(event),
            suite_name=event.suite_name,
            runs=[],
            runs_affected=0,
            transitions=dict(transitions),
        )

    # One grouped query for the current PASS counts, not one per run. `Run` is
    # joined so a row can name its model and config -- a bare id is not
    # something a reader can attribute.
    rows = session.execute(
        sa.select(
            Run.id,
            Run.model_id,
            Run.config_label,
            sa.func.count(Result.id).filter(Result.outcome == "PASS").label("pass_now"),
        )
        .select_from(Run)
        .outerjoin(Result, Result.run_id == Run.id)
        .where(Run.id.in_(moves.keys()))
        .group_by(Run.id, Run.model_id, Run.config_label)
    ).all()

    runs = [
        RegradeEventRunOut(
            run_id=row.id,
            model_id=row.model_id,
            config_label=row.config_label,
            pass_now=int(row.pass_now),
            gained=moves[row.id]["gained"],
            lost=moves[row.id]["lost"],
            pass_before=int(row.pass_now) - moves[row.id]["gained"] + moves[row.id]["lost"],
        )
        for row in rows
    ]
    runs.sort(key=lambda run: (-run.gained, -run.lost, str(run.run_id)))

    return RegradeEventDetailOut(
        event=_event_out(event),
        suite_name=event.suite_name,
        runs=runs,
        # From the recorded changes, not from `len(rows)`: a run the caller
        # cannot see under RLS drops out of the query but was still affected,
        # and reporting the visible count as the total would understate the
        # event.
        runs_affected=len(moves),
        transitions=dict(transitions),
    )
