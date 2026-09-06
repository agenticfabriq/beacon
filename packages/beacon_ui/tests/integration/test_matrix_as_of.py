"""Reading the matrix as it stood before a recorded regrade.

Two mechanisms behind one parameter, because the two column families were
stored differently. ``exact``/``got-facts`` read verdicts, which are
append-only, so as-of is a filter on when the verdict was written. ``EX`` reads
``Result.outcome``, one column overwritten in place, so it is reconstructed
backwards from what the events recorded. These tests pin both, and the places
where they can silently disagree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

import pytest
from beacon_storage.ids import uuid7
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.regrade_events import RegradeEvent
from beacon_storage.models.runs import HarnessMode, ResultStatus, Verdict, VerdictOutcome
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo

if TYPE_CHECKING:
    from datetime import datetime

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

SUITE = "asof_v1"


class _World(Protocol):
    acme_team_id: UUID
    alice_id: UUID
    alice_key: str


class _Fixture:
    """One suite, one run, and whatever outcomes a test asks for."""

    def __init__(self, session: Session, world: _World) -> None:
        self.session = session
        self.world = world
        self.solution = SolutionRepo(session).create(
            team_id=world.acme_team_id,
            solution_id="asof-sut",
            version="0.1",
            owner_team=world.acme_team_id,
            summary="",
            supported_modes=["EVAL"],
            layers=[],
            created_by=world.alice_id,
        )
        self.suite = SuiteRepo(session).create(
            team_id=world.acme_team_id,
            name=SUITE,
            description="",
            method="manual",
            suite_metadata={},
            created_by=world.alice_id,
        )
        self.items = [
            EvalItemRepo(session).create(
                tier=EvalItemTier.HUMAN_VERIFIED,
                suite=SUITE,
                team_id=world.acme_team_id,
                dataset_version="v1",
                item_input={"question": f"q{i}?", "db_id": "db"},
                gold_answer={"sql": "SELECT 1"},
                item_metadata={"difficulty": "simple", "source": "t"},
                created_by=world.alice_id,
            )
            for i in range(4)
        ]

    def run(self, model: str, outcomes: list[VerdictOutcome], *, pass_idx: int = 0) -> Any:
        run = RunRepo(self.session).create(
            team_id=self.world.acme_team_id,
            suite_id=self.suite.id,
            solution_id=self.solution.id,
            suite=SUITE,
            dataset_version="v1",
            mode=HarnessMode.EVAL,
            pass_idx=pass_idx,
            sweep_arm=f"{model}-{pass_idx}",
            config={"model_id": model},
            model_id=model,
            config_label="baseline",
            config_digest=f"digest-{model}",
            created_by=self.world.alice_id,
        )
        self.results = []
        for item, outcome in zip(self.items, outcomes, strict=False):
            self.results.append(
                ResultRepo(self.session).create(
                    team_id=self.world.acme_team_id,
                    run_id=run.id,
                    item_id=str(item.item_id),
                    attempt_idx=0,
                    output={"sql": "SELECT 1"},
                    output_kind="sql",
                    tokens_input=1,
                    tokens_output=1,
                    runtime_ms=1,
                    status=ResultStatus.COMPLETED,
                    outcome=outcome,
                    error="boom" if outcome is VerdictOutcome.ERROR else None,
                )
            )
        self.session.flush()
        return run

    def verdict(
        self,
        result_id: UUID,
        metric: str,
        value: bool,
        *,
        at: datetime,
        version: str = "v1",
    ) -> None:
        """A reading, written AT a chosen moment and at a grader version.

        ``created_at`` is set explicitly rather than defaulted, because the
        whole point of the as-of cutoff is which side of the event a verdict
        falls on, and a test that lets the database stamp them all `now()`
        cannot place any of them before it.

        ``version`` is a parameter because 0019 made verdicts unique per
        ``(result, metric, grader, grader_version)``: two eras of readings on
        one result differ by VERSION, which is exactly what a regrade produces
        -- it appends at its own version and leaves the old one standing.
        """
        self.session.add(
            Verdict(
                id=uuid7(),
                team_id=self.world.acme_team_id,
                result_id=result_id,
                metric=metric,
                grader="g",
                grader_version=version,
                criterion="correctness",
                bool_value=value,
                raw_output={},
                created_at=at,
            )
        )
        self.session.flush()

    def event(self, *, changes: list[dict[str, object]], at: datetime) -> RegradeEvent:
        event = RegradeEvent(
            id=uuid7(),
            team_id=self.world.acme_team_id,
            suite_id=self.suite.id,
            suite_name=SUITE,
            grader="result_set_match",
            grader_version="v8",
            headline_metric="exact_match",
            n_runs=1,
            n_graded=len(self.items),
            n_flipped=len(changes),
            outcome_changes=changes,
            reason="test",
            created_at=at,
        )
        self.session.add(event)
        self.session.flush()
        return event


@pytest.fixture
def fx(session: Session, world: _World) -> _Fixture:
    return _Fixture(session, world)


def _matrix(api_client: TestClient, world: _World, suite_id: UUID, **params: str) -> dict[str, Any]:
    response = api_client.get(
        f"/v1/suites/{suite_id}/results-matrix",
        params=params,
        headers={"X-API-Key": world.alice_key},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _change(result_id: UUID, run_id: UUID, before: str, after: str) -> dict[str, object]:
    return {
        "result_id": str(result_id),
        "run_id": str(run_id),
        "item_id": "x",
        "before": before,
        "after": after,
        "source": "graded_at_this_version",
    }


# --------------------------------------------------------------------------
# milestone 3: the headline rewinds
# --------------------------------------------------------------------------


def test_without_an_as_of_nothing_is_rewound(
    api_client: TestClient, world: _World, fx: _Fixture
) -> None:
    """The default path must be untouched by all of this."""
    fx.run("m", [VerdictOutcome.PASS, VerdictOutcome.FAIL])
    fx.session.commit()

    body = _matrix(api_client, world, fx.suite.id)

    assert body["as_of"] is None
    (row,) = body["rows"]
    assert row["ex_rate"] == pytest.approx(0.5)
    assert row["current"] is None, "no as-of means no second set of numbers to compare"
    assert row["affected"] is None


def test_the_headline_reads_the_outcome_the_event_recorded(
    api_client: TestClient, world: _World, fx: _Fixture
) -> None:
    """Two of four moved FAIL -> PASS, so EX was 2/4 and is now 4/4."""
    from datetime import UTC, datetime

    run = fx.run(
        "m",
        [
            VerdictOutcome.PASS,
            VerdictOutcome.PASS,
            VerdictOutcome.PASS,
            VerdictOutcome.PASS,
        ],
    )
    at = datetime(2026, 9, 1, tzinfo=UTC)
    event = fx.event(
        changes=[
            _change(fx.results[0].id, run.id, "FAIL", "PASS"),
            _change(fx.results[1].id, run.id, "FAIL", "PASS"),
        ],
        at=at,
    )
    fx.session.commit()

    body = _matrix(api_client, world, fx.suite.id, as_of_event=str(event.id))

    (row,) = body["rows"]
    assert row["ex_rate"] == pytest.approx(0.5), "as of before the event, 2 of 4 passed"
    assert row["current"]["ex_rate"] == pytest.approx(1.0), "all four pass today"
    assert row["current"]["wrong_rate"] == pytest.approx(0.0)
    assert row["wrong_rate"] == pytest.approx(0.5), "the two that moved were FAIL"
    assert row["affected"] is True
    assert body["as_of"]["grader_version"] == "v8"


def test_an_unchanged_row_reports_its_full_denominator(
    api_client: TestClient, world: _World, fx: _Fixture
) -> None:
    """The outer-join property, and the one most worth pinning.

    A result the event never touched has no reversal row. Joined INNER, every
    such result vanishes and the rate is computed over the movers alone --
    a row where 1 of 4 moved would report 0% or 100% instead of 75%.
    """
    from datetime import UTC, datetime

    run = fx.run(
        "m",
        [
            VerdictOutcome.PASS,
            VerdictOutcome.PASS,
            VerdictOutcome.PASS,
            VerdictOutcome.FAIL,
        ],
    )
    event = fx.event(
        changes=[_change(fx.results[0].id, run.id, "FAIL", "PASS")],
        at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    fx.session.commit()

    body = _matrix(api_client, world, fx.suite.id, as_of_event=str(event.id))

    (row,) = body["rows"]
    assert row["n_graded"] == 4, "all four results are still in the denominator"
    assert row["ex_rate"] == pytest.approx(0.5), "2 of 4 passed before the event"
    assert row["current"]["ex_rate"] == pytest.approx(0.75)


def test_a_row_the_event_did_not_touch_says_so(
    api_client: TestClient, world: _World, fx: _Fixture
) -> None:
    """``affected`` is read from the records, not inferred from the rates.

    This is what renders as "not affected" rather than a delta of zero, and
    the two are different claims: a row can have two changes that cancel.
    """
    from datetime import UTC, datetime

    touched = fx.run("touched", [VerdictOutcome.PASS, VerdictOutcome.PASS])
    touched_results = list(fx.results)
    fx.run("untouched", [VerdictOutcome.PASS, VerdictOutcome.FAIL])
    event = fx.event(
        changes=[_change(touched_results[0].id, touched.id, "FAIL", "PASS")],
        at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    fx.session.commit()

    body = _matrix(api_client, world, fx.suite.id, as_of_event=str(event.id))

    rows = {row["model_id"]: row for row in body["rows"]}
    assert rows["touched"]["affected"] is True
    assert rows["untouched"]["affected"] is False
    assert rows["untouched"]["ex_rate"] == pytest.approx(rows["untouched"]["current"]["ex_rate"]), (
        "an untouched row reads the same on both sides"
    )


def test_two_changes_that_cancel_still_report_the_row_as_affected(
    api_client: TestClient, world: _World, fx: _Fixture
) -> None:
    """The reason ``affected`` cannot be derived from the numbers.

    One PASS gained and one lost leaves the rate identical. Inferring
    "not affected" from that equality would tell the reader this event passed
    the row by, when it changed two of its results.
    """
    from datetime import UTC, datetime

    run = fx.run("m", [VerdictOutcome.PASS, VerdictOutcome.FAIL])
    event = fx.event(
        changes=[
            _change(fx.results[0].id, run.id, "FAIL", "PASS"),
            _change(fx.results[1].id, run.id, "PASS", "FAIL"),
        ],
        at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    fx.session.commit()

    body = _matrix(api_client, world, fx.suite.id, as_of_event=str(event.id))

    (row,) = body["rows"]
    assert row["ex_rate"] == pytest.approx(row["current"]["ex_rate"]), (
        "the rate is unchanged, which is exactly the trap"
    )
    assert row["affected"] is True, "but two of its results moved"


def test_a_result_crossing_the_error_boundary_moves_the_denominator(
    api_client: TestClient, world: _World, fx: _Fixture
) -> None:
    """ERROR leaves the denominator, so an ERROR -> PASS moves both halves.

    It did not happen in the first real event, and a rewind that tracked only
    the numerator would have been indistinguishable from a correct one there.
    """
    from datetime import UTC, datetime

    run = fx.run("m", [VerdictOutcome.PASS, VerdictOutcome.PASS])
    event = fx.event(
        changes=[_change(fx.results[0].id, run.id, "ERROR", "PASS")],
        at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    fx.session.commit()

    body = _matrix(api_client, world, fx.suite.id, as_of_event=str(event.id))

    (row,) = body["rows"]
    assert row["n_graded"] == 1, "the errored result was outside the denominator then"
    assert row["current"]["n_graded"] == 2, "and inside it now"
    assert row["ex_rate"] == pytest.approx(1.0)
    assert row["current"]["ex_rate"] == pytest.approx(1.0)
    assert row["n_errors"] == 1, "as-of, one result was an infra error"


def test_the_oldest_before_value_wins_across_two_events(
    api_client: TestClient, world: _World, fx: _Fixture
) -> None:
    """Rewinding past two events must land on the original, not the middle.

    A result that moved FAIL -> DEFER and then DEFER -> PASS was FAIL before
    the first event. Taking the newest recorded before-value would report
    DEFER -- the intermediate state -- as though it were where it started.
    """
    from datetime import UTC, datetime

    run = fx.run("m", [VerdictOutcome.PASS, VerdictOutcome.PASS])
    first = fx.event(
        changes=[_change(fx.results[0].id, run.id, "FAIL", "DEFER")],
        at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    fx.event(
        changes=[_change(fx.results[0].id, run.id, "DEFER", "PASS")],
        at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    fx.session.commit()

    body = _matrix(api_client, world, fx.suite.id, as_of_event=str(first.id))

    (row,) = body["rows"]
    assert row["wrong_rate"] == pytest.approx(0.5), "it was FAIL before the first event"
    assert row["defer_rate"] == pytest.approx(0.0), (
        "DEFER was the intermediate state and must not be reported as the original"
    )


# --------------------------------------------------------------------------
# milestone 2: the readings rewind too
# --------------------------------------------------------------------------


def test_the_readings_rewind_to_the_verdict_that_existed_then(
    api_client: TestClient, world: _World, fx: _Fixture
) -> None:
    """``exact`` reads the newest verdict written BEFORE the event.

    Verdicts are append-only, so both eras are still on the row: the old
    reading and the one the regrade appended. Reading the newest regardless
    would show today's number in a rewound column.
    """
    from datetime import UTC, datetime

    fx.run("m", [VerdictOutcome.PASS, VerdictOutcome.PASS])
    at = datetime(2026, 9, 1, tzinfo=UTC)
    # Before the event: neither result matched exactly.
    old = datetime(2026, 8, 1, tzinfo=UTC)
    fx.verdict(fx.results[0].id, "exact_match", False, at=old, version="v7")
    fx.verdict(fx.results[1].id, "exact_match", False, at=old, version="v7")
    # The regrade appends new readings at its own version, in the event's own
    # transaction. Both eras survive on the row -- that is why this is the
    # cheap half of the rewind.
    fx.verdict(fx.results[0].id, "exact_match", True, at=at, version="v8")
    fx.verdict(fx.results[1].id, "exact_match", True, at=at, version="v8")
    event = fx.event(changes=[], at=at)
    fx.session.commit()

    body = _matrix(api_client, world, fx.suite.id, as_of_event=str(event.id))

    (row,) = body["rows"]
    assert row["exact_rate"] == pytest.approx(0.0), "as of before the event, neither matched"
    assert row["current"]["exact_rate"] == pytest.approx(1.0), "both match today"


def test_a_reading_written_by_the_event_itself_is_excluded(
    api_client: TestClient, world: _World, fx: _Fixture
) -> None:
    """The cutoff is strict, and that is the whole reason it is strict.

    A regrade writes its verdicts and its event in one transaction, so both
    carry the same ``now()``. ``<=`` would include the verdicts that regrade
    produced and the readings would contradict the outcomes beside them.
    """
    from datetime import UTC, datetime

    fx.run("m", [VerdictOutcome.PASS])
    at = datetime(2026, 9, 1, tzinfo=UTC)
    fx.verdict(fx.results[0].id, "exact_match", True, at=at)
    event = fx.event(changes=[], at=at)
    fx.session.commit()

    body = _matrix(api_client, world, fx.suite.id, as_of_event=str(event.id))

    (row,) = body["rows"]
    assert row["exact_rate"] is None, (
        "the only reading was written by this event, so as-of there was none -- "
        "None, not 0.0, because nothing was measured"
    )
    assert row["current"]["exact_rate"] == pytest.approx(1.0)


def test_a_row_with_no_reading_then_reports_none_not_zero(
    api_client: TestClient, world: _World, fx: _Fixture
) -> None:
    """Absent and false are different, and this is where they get confused."""
    from datetime import UTC, datetime

    fx.run("m", [VerdictOutcome.PASS, VerdictOutcome.FAIL])
    event = fx.event(changes=[], at=datetime(2026, 9, 1, tzinfo=UTC))
    fx.session.commit()

    body = _matrix(api_client, world, fx.suite.id, as_of_event=str(event.id))

    (row,) = body["rows"]
    assert row["exact_rate"] is None
    assert row["got_facts_rate"] is None


# --------------------------------------------------------------------------
# scoping
# --------------------------------------------------------------------------


def test_an_unknown_event_is_refused(api_client: TestClient, world: _World, fx: _Fixture) -> None:
    fx.run("m", [VerdictOutcome.PASS])
    fx.session.commit()

    response = api_client.get(
        f"/v1/suites/{fx.suite.id}/results-matrix",
        params={"as_of_event": str(uuid4())},
        headers={"X-API-Key": world.alice_key},
    )
    assert response.status_code == 404, response.text


def test_an_event_from_another_benchmark_is_refused(
    api_client: TestClient, world: _World, fx: _Fixture, session: Session
) -> None:
    """Scoped by suite, so a pasted id cannot rewind one benchmark by another.

    An event from another suite has its own before-values for its own results;
    applying them here would silently mix two corpora.
    """
    fx.run("m", [VerdictOutcome.PASS])
    other = SuiteRepo(session).create(
        team_id=world.acme_team_id,
        name="asof_other",
        description="",
        method="manual",
        suite_metadata={},
        created_by=world.alice_id,
    )
    stray = RegradeEvent(
        id=uuid7(),
        team_id=world.acme_team_id,
        suite_id=other.id,
        suite_name="asof_other",
        grader="g",
        grader_version="v1",
        headline_metric="exact_match",
        n_runs=0,
        n_graded=0,
        n_flipped=0,
        outcome_changes=[],
        reason=None,
    )
    session.add(stray)
    session.commit()

    response = api_client.get(
        f"/v1/suites/{fx.suite.id}/results-matrix",
        params={"as_of_event": str(stray.id)},
        headers={"X-API-Key": world.alice_key},
    )
    assert response.status_code == 404, response.text
