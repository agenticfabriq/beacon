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
from beacon_ui.api.routes.matrix import _row_key

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


# --------------------------------------------------------------------------
# row_key: naming one row in a URL
# --------------------------------------------------------------------------


def test_row_key_separates_rows_that_share_a_config_digest(
    api_client: TestClient, world: _World, fx: _Fixture
) -> None:
    """The digest cannot name a row, and this is why.

    ``config_digest`` hashes the run CONFIG, and ``model_id`` is not in the
    config -- so two models under one config are two rows with one digest.
    Measured on the real corpus: one spider2 digest is shared by four rows, and
    a cell page keyed on it restored the right configuration for one of the
    four. ``row_key`` is derived from the full group tuple instead.
    """
    for model in ("model-a", "model-b"):
        run = RunRepo(fx.session).create(
            team_id=world.acme_team_id,
            suite_id=fx.suite.id,
            solution_id=fx.solution.id,
            suite=SUITE,
            dataset_version="v1",
            mode=HarnessMode.EVAL,
            pass_idx=0,
            sweep_arm=model,
            config={"engine": "duckdb"},
            model_id=model,
            config_label="shared",
            # The SAME digest for both, which is what the real data does when
            # two models run one configuration.
            config_digest="one-digest",
            created_by=world.alice_id,
        )
        ResultRepo(fx.session).create(
            team_id=world.acme_team_id,
            run_id=run.id,
            item_id=str(fx.items[0].item_id),
            attempt_idx=0,
            output={"sql": "SELECT 1"},
            output_kind="sql",
            tokens_input=1,
            tokens_output=1,
            runtime_ms=1,
            status=ResultStatus.COMPLETED,
            outcome=VerdictOutcome.PASS,
            error=None,
        )
    fx.session.commit()

    body = _matrix(api_client, world, fx.suite.id)

    rows = body["rows"]
    assert len(rows) == 2, "two models under one config are two rows"
    assert len({row["config_digest"] for row in rows}) == 1, (
        "the premise: both rows carry the same digest"
    )
    keys = {row["row_key"] for row in rows}
    assert len(keys) == 2, f"row_key must separate them, or a cell URL opens the wrong row: {keys}"
    assert all(row["row_key"] for row in rows), "every row needs a handle"


def test_row_key_is_unique_across_every_row(
    api_client: TestClient, world: _World, fx: _Fixture
) -> None:
    """One key per row, by construction -- asserted rather than assumed.

    The key is a hash of six of the eight expressions the statement groups by;
    the two omitted are functionally determined by ``solution_id``, which is
    included, so uniqueness still follows from the grouping. That reasoning is only as good as the
    two staying in step, which is what this checks.
    """
    for index, model in enumerate(("a", "b", "c")):
        for label in ("one", "two"):
            run = RunRepo(fx.session).create(
                team_id=world.acme_team_id,
                suite_id=fx.suite.id,
                solution_id=fx.solution.id,
                suite=SUITE,
                dataset_version="v1",
                mode=HarnessMode.EVAL,
                pass_idx=index,
                sweep_arm=f"{model}-{label}",
                config={"engine": "duckdb", "retrieval_k": index},
                model_id=model,
                config_label=label,
                config_digest=f"d-{label}",
                created_by=world.alice_id,
            )
            ResultRepo(fx.session).create(
                team_id=world.acme_team_id,
                run_id=run.id,
                item_id=str(fx.items[0].item_id),
                attempt_idx=0,
                output={"sql": "SELECT 1"},
                output_kind="sql",
                tokens_input=1,
                tokens_output=1,
                runtime_ms=1,
                status=ResultStatus.COMPLETED,
                outcome=VerdictOutcome.PASS,
                error=None,
            )
    fx.session.commit()

    body = _matrix(api_client, world, fx.suite.id)

    keys = [row["row_key"] for row in body["rows"]]
    assert len(keys) == len(set(keys)), f"{len(keys)} rows produced {len(set(keys))} distinct keys"
    assert len(body["rows"]) == 6, "three models x two configs"


def test_every_field_of_the_row_key_is_load_bearing() -> None:
    """Dropping ANY of the six collides two rows the matrix keeps apart.

    Written because the two tests above do not do this. Both vary only
    ``model_id`` and ``config_label``, and everything else they set is
    functionally determined by those -- so removing ``solution_id``,
    ``config_digest``, ``engine`` or ``retrieval_k`` from the key left both
    green. ``config_digest`` is the sharpest: the matrix's own comment says a
    knob present only in the digest splits a row, so dropping it collides two
    genuinely distinct configurations and the cell page opens the wrong one.

    A unit test on the function, deliberately. The integration tests can only
    reach fields the seeding varies, and reaching all six through seeded runs
    means six near-identical fixtures for something the function answers
    directly.
    """
    base = {
        "solution_id": "sol-1",
        "model_id": "model-1",
        "config_label": "label-1",
        "config_digest": "digest-1",
        "engine": "duckdb",
        "retrieval_k": "8",
    }
    reference = _row_key(**base)

    for field in base:
        varied = dict(base)
        varied[field] = "OTHER"
        assert _row_key(**varied) != reference, (
            f"{field} does not affect row_key, so two rows differing only in it "
            "share a handle and the wrong one opens"
        )


def test_the_row_key_keeps_absent_and_empty_apart() -> None:
    """NULL and '' are different groups, so they must be different keys.

    ``model_id`` and ``config_label`` are both nullable. The GROUP BY treats
    NULL and the empty string as distinct groups, so a key built with
    ``str(x or "")`` -- which an earlier version used -- gave those two rows
    one handle.
    """
    for field in ("model_id", "config_label", "engine", "retrieval_k"):
        base = {
            "solution_id": "sol",
            "model_id": "m",
            "config_label": "l",
            "config_digest": "d",
            "engine": "e",
            "retrieval_k": "1",
        }
        absent = dict(base, **{field: None})
        empty = dict(base, **{field: ""})
        assert _row_key(**absent) != _row_key(**empty), (
            f"an absent {field} and an empty {field} share a key"
        )


def test_the_row_key_cannot_be_forged_by_a_field_that_looks_like_a_boundary() -> None:
    """A value cannot impersonate a field boundary, whatever bytes it holds.

    ``model_id`` and ``config_label`` are plain ``String(200)`` fed from the
    run-create payload, so any byte can appear in them. An earlier version
    joined the fields on US (0x1f) and asserted in prose that no value could
    contain one -- measured, ``model_id='a\\x1fb'`` and ``config_label='b\\x1fc'``
    produced the same key, so two rows the GROUP BY keeps apart shared a handle
    and the cell URL opened the other configuration.

    Length-prefixing fixes it whatever the bytes are. The cases below include
    values crafted against the PREFIX itself (``'1:a'``), because a separator
    scheme is only as good as the thing a field cannot contain, and the answer
    here has to be "nothing at all".
    """
    fields = ("solution_id", "model_id", "config_label", "config_digest")
    base = {
        "solution_id": "sol",
        "model_id": "m",
        "config_label": "l",
        "config_digest": "d",
        "engine": "e",
        "retrieval_k": "1",
    }
    hostile = ("a\x1fb", "a:b", "1:a", "", "6:sol", "\x00")
    keys: dict[str, tuple[str, str]] = {}
    for field in fields:
        for value in hostile:
            key = _row_key(**dict(base, **{field: value}))
            assert key not in keys, (
                f"{field}={value!r} collides with {keys[key][0]}={keys[key][1]!r}: "
                "two rows the matrix keeps apart would share one URL"
            )
            keys[key] = (field, value)

    # And the pair that actually collided, named explicitly so a regression is
    # recognisable rather than just one of many.
    assert _row_key("sol", "a\x1fb", "c", "d", "e", "1") != _row_key(
        "sol", "a", "b\x1fc", "d", "e", "1"
    ), "the separator collision is back"


# --------------------------------------------------------------------------
# attribution: a delta that a LATER regrade caused is not this event's doing
# --------------------------------------------------------------------------


def test_a_later_regrade_is_not_attributed_to_the_selected_event(
    api_client: TestClient, world: _World, fx: _Fixture
) -> None:
    """Selecting the older of two events must not claim the younger one's work.

    The reversal deliberately admits the selected event AND every later one --
    it has to, or the rewound number would not account for everything that
    happened since. But `affected` and its tooltip make the narrower claim,
    that THIS event moved something in this row, and one count was answering
    both questions. So a configuration only the second regrade touched was
    reported as the first one's doing, on the one screen whose entire purpose
    is saying where a number came from.

    Two rows, one event each, and the OLDER event selected:

    * row `a` -- moved by the selected event
    * row `b` -- moved only by a later event

    Both have a real delta. Only `a` is this event's.
    """
    from datetime import UTC, datetime

    first_at = datetime(2026, 3, 1, tzinfo=UTC)
    second_at = datetime(2026, 4, 1, tzinfo=UTC)

    run_a = fx.run("a", [VerdictOutcome.PASS] * 4)
    results_a = list(fx.results)
    run_b = fx.run("b", [VerdictOutcome.PASS] * 4)
    results_b = list(fx.results)

    # Readings on both sides of each cutoff, so the EX columns have something
    # to rewind to rather than reading None.
    for results in (results_a, results_b):
        for result in results:
            fx.verdict(result.id, "exact_match", True, at=datetime(2026, 1, 1, tzinfo=UTC))

    first = fx.event(changes=[_change(results_a[0].id, run_a.id, "FAIL", "PASS")], at=first_at)
    fx.event(changes=[_change(results_b[0].id, run_b.id, "FAIL", "PASS")], at=second_at)
    fx.session.commit()

    body = _matrix(api_client, world, fx.suite.id, as_of_event=str(first.id))
    rows = {row["model_id"]: row for row in body["rows"]}

    assert rows["a"]["affected"] is True
    assert rows["a"]["changed_since"] is True

    assert rows["b"]["affected"] is False, (
        "the selected event recorded no change to row b -- claiming otherwise attributes "
        "a later regrade's work to the event the reader picked"
    )
    assert rows["b"]["changed_since"] is True, (
        "row b DID move since the selected point, so it has a real delta and must not "
        "be labelled 'not affected' either"
    )

    # And the rewound numbers are right for both: one of four results held FAIL
    # before its own event, so each row reads 3/4 then against 4/4 now.
    for model in ("a", "b"):
        assert rows[model]["ex_rate"] == pytest.approx(0.75), model
        assert rows[model]["current"]["ex_rate"] == pytest.approx(1.0), model


def test_selecting_the_only_event_attributes_everything_to_it(
    api_client: TestClient, world: _World, fx: _Fixture
) -> None:
    """The common case, which is also why the bug stayed invisible.

    With one recorded event the reversal admits only that event, so the broad
    and narrow counts agree and the old single flag was right. Production had
    exactly one event per suite when this was found.
    """
    from datetime import UTC, datetime

    run = fx.run("a", [VerdictOutcome.PASS] * 4)
    for result in fx.results:
        fx.verdict(result.id, "exact_match", True, at=datetime(2026, 1, 1, tzinfo=UTC))
    only = fx.event(
        changes=[_change(fx.results[0].id, run.id, "FAIL", "PASS")],
        at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    fx.session.commit()

    (row,) = _matrix(api_client, world, fx.suite.id, as_of_event=str(only.id))["rows"]
    assert row["affected"] is True
    assert row["changed_since"] is True


def test_the_row_handle_is_wide_enough_to_survive_a_search() -> None:
    """128 bits, hardening a handle two of whose six fields are caller-chosen.

    The original 64 was argued from ACCIDENT -- far past collision range for a
    table of tens of rows, which is true. But ``config_label`` and the config
    behind ``config_digest`` come from the run-create payload, so somebody who
    can post runs chooses their own inputs, and a 64-bit digest puts an
    any-pair birthday search at roughly 2**32 offline evaluations.

    What that reaches is bounded, and ``_row_key`` states it at length: both
    halves of a found pair are rows the searcher created, so the outcome is a
    link naming one of their own configurations that opens another. Colliding
    with somebody ELSE's row is a second preimage, which 64 bits already put at
    2**64. This width is hardening against a self-collision, not the closing of
    an open door -- said here too, because a reader who finds only this test
    would otherwise take it for the latter.

    Pinned as a NUMBER rather than a comment, because the truncation is one
    character to change and nothing else would notice.
    """
    key = _row_key(uuid7(), "m", "baseline", "digest", "e", "8")
    assert len(key) == 32, (
        f"the row handle is {len(key)} hex characters, {len(key) * 4} bits; a "
        "birthday search costs about 2**" + str(len(key) * 2) + " evaluations"
    )
    # The hex check IS `int(..., 16)` raising; there is nothing to compare it
    # against, so it is not dressed up as a comparison.
    int(key, 16)
