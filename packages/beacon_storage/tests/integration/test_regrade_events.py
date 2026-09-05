"""The record of a regrade cannot disagree with its own evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from beacon_storage.models.regrade_events import RegradeEvent
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _event(**over: object) -> RegradeEvent:
    base: dict[str, object] = {
        "suite_name": "bird_minidev_v2",
        "grader": "result_set_match",
        "grader_version": "v8",
        "headline_metric": "exact_match",
        "n_runs": 42,
        "n_graded": 7747,
        "n_flipped": 1,
        "outcome_changes": [
            {"result_id": "r1", "run_id": "run1", "before": "FAIL", "after": "PASS"}
        ],
        "reason": "narrowed the orderless refusal",
    }
    base.update(over)
    return RegradeEvent(**base)


def test_a_consistent_event_is_stored(session: Session) -> None:
    session.add(_event())
    session.commit()

    stored = session.scalars(sa.select(RegradeEvent)).one()
    assert stored.n_flipped == 1
    assert stored.outcome_changes[0]["before"] == "FAIL"
    assert stored.reason == "narrowed the orderless refusal"


@pytest.mark.parametrize(
    ("n_flipped", "changes", "why"),
    [
        (2, [{"result_id": "r1"}], "count claims more than the evidence"),
        (0, [{"result_id": "r1"}], "evidence exists but the count says nothing moved"),
        (1, [], "count claims a flip with no record of it"),
    ],
)
def test_a_count_that_disagrees_with_its_evidence_is_refused(
    session: Session, n_flipped: int, changes: list[dict[str, str]], why: str
) -> None:
    """This is the failure the table exists to prevent, so the DB refuses it.

    A summary that says "3 flipped" over 93 real changes is exactly how B75
    certified 90 wrong rows as correct. Here the count and the changes are the
    same fact stored twice, and the constraint keeps them one fact.
    """
    session.add(_event(n_flipped=n_flipped, outcome_changes=changes))

    with pytest.raises(IntegrityError, match="ck_regrade_event_flips_match_changes"):
        session.commit()
    session.rollback()


def test_an_event_that_changed_nothing_is_still_recordable(session: Session) -> None:
    """ "I ran it and nothing moved" is a fact worth keeping.

    Without it, silence is indistinguishable from never having run -- which is
    the whole complaint that produced this table.
    """
    session.add(_event(n_flipped=0, outcome_changes=[], reason="idempotency check"))
    session.commit()

    assert session.scalars(sa.select(RegradeEvent)).one().n_flipped == 0


def test_a_negative_count_is_refused(session: Session) -> None:
    session.add(_event(n_skipped=-1))

    with pytest.raises(IntegrityError, match="ck_regrade_event_counts_non_negative"):
        session.commit()
    session.rollback()
