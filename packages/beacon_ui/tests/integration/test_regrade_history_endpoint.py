"""The History endpoints: what moved this benchmark's numbers, and for whom.

These read ``regrade_events``, which 0022 created without tenancy on the
grounds that no route served it. 0024 added ``team_id`` and RLS precisely
because these endpoints exist, so the cross-tenant test here is not belt and
braces -- it is the test that the reason for 0024 actually holds.
"""

from typing import Protocol
from uuid import UUID, uuid4

import pytest
from beacon_storage.ids import uuid7
from beacon_storage.models.regrade_events import RegradeEvent
from beacon_storage.models.runs import ResultStatus, VerdictOutcome
from beacon_storage.repository.results import ResultRepo
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _World(Protocol):
    acme_team_id: UUID
    globex_team_id: UUID
    acme_suite_id: UUID
    globex_suite_id: UUID
    acme_run_id: UUID
    alice_key: str
    carol_key: str


def _result(session: Session, world: _World, item: str, outcome: VerdictOutcome) -> UUID:
    row = ResultRepo(session).create(
        team_id=world.acme_team_id,
        run_id=world.acme_run_id,
        item_id=item,
        attempt_idx=0,
        output={"sql": "SELECT 1"},
        output_kind="sql",
        tokens_input=None,
        tokens_output=None,
        runtime_ms=1,
        status=ResultStatus.COMPLETED,
        outcome=outcome,
        error=None,
    )
    return row.id


def _event(
    session: Session,
    world: _World,
    *,
    changes: list[dict[str, str]],
    suite_id: UUID | None = None,
    team_id: UUID | None = None,
    reason: str | None = "B77 orderless refusal narrowed",
) -> RegradeEvent:
    event = RegradeEvent(
        id=uuid7(),
        team_id=team_id or world.acme_team_id,
        suite_id=suite_id or world.acme_suite_id,
        suite_name="bird_minidev_v2",
        grader="result_set_match",
        grader_version="v8",
        headline_metric="exact_match",
        n_runs=1,
        n_graded=10,
        n_already_current=0,
        n_skipped=0,
        n_refused=0,
        # The table's own CHECK requires this to equal the recorded changes, so
        # a test that got it wrong would fail at flush rather than assert.
        n_flipped=len(changes),
        outcome_changes=changes,
        reason=reason,
    )
    session.add(event)
    session.commit()
    return event


def test_history_lists_recorded_events(
    api_client: TestClient, world: _World, session: Session
) -> None:
    _event(session, world, changes=[])

    response = api_client.get(
        f"/v1/suites/{world.acme_suite_id}/history",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["grader_version"] == "v8"
    assert body[0]["headline_metric"] == "exact_match"
    assert body[0]["reason"] == "B77 orderless refusal narrowed"


def test_history_is_empty_when_nothing_is_recorded(api_client: TestClient, world: _World) -> None:
    """An empty list, and a 200 -- not a 404.

    "Nothing recorded" and "no such benchmark" are different facts, and the UI
    has to tell them apart to render the empty state honestly.
    """
    response = api_client.get(
        f"/v1/suites/{world.acme_suite_id}/history",
        headers={"X-API-Key": world.alice_key},
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_history_404s_for_an_unknown_suite(api_client: TestClient, world: _World) -> None:
    response = api_client.get(
        f"/v1/suites/{uuid4()}/history",
        headers={"X-API-Key": world.alice_key},
    )
    assert response.status_code in (403, 404), response.text


def test_another_team_cannot_read_this_benchmarks_history(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """The reason 0024 exists. Carol is on globex; the event belongs to acme."""
    _event(session, world, changes=[])

    response = api_client.get(
        f"/v1/suites/{world.acme_suite_id}/history",
        headers={"X-API-Key": world.carol_key},
    )

    # Either the permission layer refuses the suite or RLS hides the rows.
    # Both are correct; leaking the event is not.
    if response.status_code == 200:
        assert response.json() == [], "globex read acme's regrade history"
    else:
        assert response.status_code in (403, 404), response.text


def test_detail_reports_pass_before_as_arithmetic_on_screen(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """``pass_before`` = ``pass_now`` - gained + lost, and a reader can check it."""
    gained = [_result(session, world, f"g{i}", VerdictOutcome.PASS) for i in range(3)]
    lost = [_result(session, world, "l0", VerdictOutcome.FAIL)]
    _result(session, world, "steady", VerdictOutcome.PASS)
    session.commit()

    event = _event(
        session,
        world,
        changes=[
            {
                "result_id": str(rid),
                "run_id": str(world.acme_run_id),
                "item_id": "x",
                "before": "FAIL",
                "after": "PASS",
                "source": "graded_at_this_version",
            }
            for rid in gained
        ]
        + [
            {
                "result_id": str(rid),
                "run_id": str(world.acme_run_id),
                "item_id": "y",
                "before": "PASS",
                "after": "FAIL",
                "source": "graded_at_this_version",
            }
            for rid in lost
        ],
    )

    response = api_client.get(
        f"/v1/suites/{world.acme_suite_id}/history/{event.id}",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runs_affected"] == 1
    (run,) = body["runs"]
    # 4 PASS live now (3 gained + 1 steady); the event gained 3 and lost 1.
    assert run["pass_now"] == 4
    assert run["gained"] == 3
    assert run["lost"] == 1
    assert run["pass_before"] == 4 - 3 + 1
    assert body["transitions"] == {"FAIL->PASS": 3, "PASS->FAIL": 1}


def test_a_move_that_does_not_touch_pass_changes_neither_count(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """DEFER -> FAIL is a real change that leaves the numerator alone.

    Counting it as a gain or a loss would make ``pass_before`` disagree with
    the arithmetic the detail page invites a reader to do, so it must appear in
    ``transitions`` and in neither counter.
    """
    rid = _result(session, world, "d0", VerdictOutcome.FAIL)
    _result(session, world, "p0", VerdictOutcome.PASS)
    session.commit()

    event = _event(
        session,
        world,
        changes=[
            {
                "result_id": str(rid),
                "run_id": str(world.acme_run_id),
                "item_id": "z",
                "before": "DEFER",
                "after": "FAIL",
                "source": "graded_at_this_version",
            }
        ],
    )

    response = api_client.get(
        f"/v1/suites/{world.acme_suite_id}/history/{event.id}",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # The run was touched, so it appears -- with both counters at zero. An
    # earlier version of the route only registered a run when a counter moved,
    # and reported this event as affecting nothing.
    assert body["runs_affected"] == 1
    (run,) = body["runs"]
    assert run["gained"] == 0
    assert run["lost"] == 0
    assert run["pass_before"] == run["pass_now"] == 1
    assert body["transitions"] == {"DEFER->FAIL": 1}


def test_detail_refuses_an_event_from_another_benchmark(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """The suite is part of the lookup, not a check applied afterwards.

    Both suites here belong to teams the seeded world can reach, so RLS does
    not answer this one -- only the repository's suite-scoped query does.
    """
    event = _event(session, world, changes=[], suite_id=world.acme_suite_id)

    response = api_client.get(
        f"/v1/suites/{world.globex_suite_id}/history/{event.id}",
        headers={"X-API-Key": world.carol_key},
    )

    assert response.status_code in (403, 404), response.text


def test_an_event_with_no_reason_reports_none_not_empty_string(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """`None` and `""` are different: one says nothing was given."""
    _event(session, world, changes=[], reason=None)

    response = api_client.get(
        f"/v1/suites/{world.acme_suite_id}/history",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 200, response.text
    assert response.json()[0]["reason"] is None
