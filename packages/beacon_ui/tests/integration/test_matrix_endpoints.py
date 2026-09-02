"""The results matrix and the questions listing behind the v3 UI.

The matrix is the page the hand-maintained tracker becomes, so its semantics
are the register's: ERROR leaves the denominator, DEFER stays in it, a rate
over nothing gradeable is None, and an invalidated run is out of everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

import pytest
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.runs import HarnessMode, ResultStatus, VerdictOutcome
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

SUITE = "matrix_v1"


class _World(Protocol):
    acme_team_id: UUID
    alice_id: UUID
    alice_key: str


@dataclass(frozen=True)
class Seeded:
    suite_id: str
    invalidated_run_id: str


@pytest.fixture
def seeded(session: Session, world: _World) -> Seeded:
    solution = SolutionRepo(session).create(
        team_id=world.acme_team_id,
        solution_id="matrix-sut",
        version="0.1",
        owner_team=world.acme_team_id,
        summary="",
        supported_modes=["EVAL"],
        layers=[],
        created_by=world.alice_id,
    )
    suite = SuiteRepo(session).create(
        team_id=world.acme_team_id,
        name=SUITE,
        description="",
        method="manual",
        suite_metadata={},
        created_by=world.alice_id,
    )
    items = []
    for index, difficulty in enumerate(["simple", "simple", "challenging"]):
        item = EvalItemRepo(session).create(
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite=SUITE,
            team_id=world.acme_team_id,
            dataset_version="v1",
            item_input={"question": f"q{index}?", "db_id": "db"},
            gold_answer={"sql": "SELECT 1"},
            item_metadata={"difficulty": difficulty, "source": "test-corpus"},
            created_by=world.alice_id,
        )
        items.append(item)

    def _run(model: str, outcomes: list[VerdictOutcome | None], *, pass_idx: int) -> str:
        run = RunRepo(session).create(
            team_id=world.acme_team_id,
            suite_id=suite.id,
            solution_id=solution.id,
            suite=SUITE,
            dataset_version="v1",
            mode=HarnessMode.EVAL,
            pass_idx=pass_idx,
            # Model is not part of run identity (B7's tuple), so two models at
            # the same pass need distinct arms -- the API path gets this for
            # free from a fresh parent_sweep_id per registration.
            sweep_arm=model,
            config={"model_id": model},
            model_id=model,
            config_label="baseline",
            config_digest=f"digest-{model}",
            created_by=world.alice_id,
        )
        for item, outcome in zip(items, outcomes, strict=True):
            if outcome is None:
                continue
            ResultRepo(session).create(
                team_id=world.acme_team_id,
                run_id=run.id,
                item_id=str(item.item_id),
                attempt_idx=0,
                output={"sql": "SELECT 1"},
                output_kind="sql",
                tokens_input=100,
                tokens_output=100,
                runtime_ms=1000,
                status=ResultStatus.COMPLETED,
                outcome=outcome,
                error="boom" if outcome is VerdictOutcome.ERROR else None,
            )
        return str(run.id)

    # model-a: PASS, FAIL, DEFER -> ex 1/3, defer 1/3, wrong 1/3
    _run("model-a", [VerdictOutcome.PASS, VerdictOutcome.FAIL, VerdictOutcome.DEFER], pass_idx=0)
    # model-b: PASS, PASS, ERROR -> ex 2/2 (the error leaves the denominator)
    _run("model-b", [VerdictOutcome.PASS, VerdictOutcome.PASS, VerdictOutcome.ERROR], pass_idx=0)
    # an invalidated model-b run full of FAILs, which must not drag the row down
    bad = _run(
        "model-b", [VerdictOutcome.FAIL, VerdictOutcome.FAIL, VerdictOutcome.FAIL], pass_idx=1
    )
    RunRepo(session).invalidate(UUID(bad), user_id=world.alice_id, reason="bad harness")
    session.commit()
    return Seeded(suite_id=str(suite.id), invalidated_run_id=bad)


def _matrix(api_client: TestClient, world: _World, seeded: Seeded, **params: str) -> dict[str, Any]:
    response = api_client.get(
        f"/v1/suites/{seeded.suite_id}/results-matrix",
        params=params,
        headers={"X-API-Key": world.alice_key},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_one_row_per_configuration(api_client: TestClient, world: _World, seeded: Seeded) -> None:
    body = _matrix(api_client, world, seeded)

    assert [(row["model_id"], row["config_label"]) for row in body["rows"]] == [
        ("model-b", "baseline"),
        ("model-a", "baseline"),
    ]


def test_an_error_leaves_the_denominator(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    """An outage is not a wrong answer; model-b's EX is 2/2, not 2/3."""
    row = next(r for r in _matrix(api_client, world, seeded)["rows"] if r["model_id"] == "model-b")

    assert row["ex_rate"] == 1.0
    assert row["n_errors"] == 1
    assert row["n_graded"] == 2


def test_a_deferral_stays_in_the_denominator(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    row = next(r for r in _matrix(api_client, world, seeded)["rows"] if r["model_id"] == "model-a")

    assert row["ex_rate"] == pytest.approx(1 / 3)
    assert row["defer_rate"] == pytest.approx(1 / 3)
    assert row["wrong_rate"] == pytest.approx(1 / 3)


def test_an_invalidated_run_is_out_of_the_matrix(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    """The all-FAIL invalidated run would halve model-b's EX if it counted."""
    row = next(r for r in _matrix(api_client, world, seeded)["rows"] if r["model_id"] == "model-b")

    assert row["n_runs"] == 1
    assert row["ex_rate"] == 1.0


def test_the_matrix_filters_by_difficulty(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    body = _matrix(api_client, world, seeded, difficulty="challenging")
    row = next(r for r in body["rows"] if r["model_id"] == "model-a")

    # only the challenging item remains: model-a deferred it
    assert row["n_graded"] == 1
    assert row["defer_rate"] == 1.0


def test_facet_counts_cover_the_unfiltered_selection(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    body = _matrix(api_client, world, seeded, difficulty="challenging")

    # Questions, not attempts. Both valid runs answer the same 2 simple and 1
    # challenging item; the invalidated run's three do not appear at all.
    assert body["difficulty_counts"] == {"simple": 2, "challenging": 1}


def test_facet_counts_do_not_grow_with_the_number_of_runs(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    """The chip names a slice of the benchmark, so runs must not multiply it.

    Counting result rows made it read runs x items: a 135-question suite
    showed "all 675" once five runs existed, and a reader took that for the
    suite's size. How many attempts back each rate is n_graded's job, per row.
    """
    counts = _matrix(api_client, world, seeded)["difficulty_counts"]

    assert sum(counts.values()) == 3
    items = api_client.get(
        f"/v1/suites/{seeded.suite_id}/items",
        headers={"X-API-Key": world.alice_key},
    ).json()
    assert counts == items["difficulty_counts"]


def test_medians_are_reported(api_client: TestClient, world: _World, seeded: Seeded) -> None:
    row = _matrix(api_client, world, seeded)["rows"][0]

    assert row["median_tokens"] == 200.0
    assert row["median_runtime_ms"] == 1000.0


def test_the_questions_listing_carries_facets_and_tolerance(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    response = api_client.get(
        f"/v1/suites/{seeded.suite_id}/items",
        headers={"X-API-Key": world.alice_key},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert body["difficulty_counts"] == {"simple": 2, "challenging": 1}
    assert body["source_counts"] == {"test-corpus": 3}
    assert all(row["question"] for row in body["items"])
    assert all(row["has_gold_sql"] for row in body["items"])


def test_the_questions_listing_filters_by_difficulty(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    response = api_client.get(
        f"/v1/suites/{seeded.suite_id}/items",
        params={"difficulty": "challenging"},
        headers={"X-API-Key": world.alice_key},
    )

    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["difficulty"] == "challenging"
    # facets still describe the whole benchmark
    assert body["difficulty_counts"]["simple"] == 2


def test_got_facts_is_reported_beside_exact_match(
    api_client: TestClient, world: _World, seeded: Seeded, session: Session
) -> None:
    """The two readings beside EX, and neither one is EX.

    Both are seeded true on the PASS and the FAIL, so both read 2/3 where EX
    reads 1/3. That gap is the assertion: a reading whose numerator counts only
    PASSes is `ex_rate` under another name, and the whole point of these
    columns is that they mean the same thing on every row while EX does not.
    """
    from beacon_storage.models.runs import Result
    from beacon_storage.repository.verdicts import VerdictRepo
    from sqlalchemy import select

    results = session.scalars(select(Result).where(Result.team_id == world.acme_team_id)).all()
    for result in results:
        if str(result.outcome) not in ("PASS", "FAIL"):
            continue
        for metric in ("got_facts", "exact_match"):
            VerdictRepo(session).create(
                team_id=world.acme_team_id,
                result_id=result.id,
                grader="execution_grounded_sql",
                grader_version="v1",
                metric=metric,
                criterion="correctness",
                # everything that graded PASS or FAIL is right under both
                # readings in this seed; they diverge in
                # test_the_strict_and_tolerant_columns_report_different_readings
                bool_value=True,
                value=1.0,
                justification="seeded",
                raw_output=None,
            )
    session.commit()

    row = next(r for r in _matrix(api_client, world, seeded)["rows"] if r["model_id"] == "model-a")

    assert row["ex_rate"] == pytest.approx(1 / 3)
    # PASS + FAIL both carry true verdicts; the DEFER does not. A numerator
    # gated on PASS would read 1/3 for both and look entirely plausible.
    assert row["got_facts_rate"] == pytest.approx(2 / 3)
    assert row["exact_rate"] == pytest.approx(2 / 3)


def test_the_strict_and_tolerant_columns_report_different_readings(
    api_client: TestClient, world: _World, seeded: Seeded, session: Session
) -> None:
    """The two columns must not be able to be the same wire.

    test_got_facts_is_reported_beside_exact_match seeds both metrics
    identically, so it proves each column is not EX without proving either is
    not the OTHER: pointing the strict column's numerator at the got_facts
    verdicts passes it unchanged, and the strict column then publishes the
    tolerant number under the strict name.

    So seed them apart, which is also the real case -- tolerant forgives shape,
    strict does not, and B58's 100.5% came from exactly this overlap. Right
    facts on the PASS and the FAIL, exact match on the PASS alone.
    """
    from beacon_storage.models.runs import Result
    from beacon_storage.repository.verdicts import VerdictRepo
    from sqlalchemy import select

    results = session.scalars(select(Result).where(Result.team_id == world.acme_team_id)).all()
    for result in results:
        outcome = str(result.outcome)
        if outcome not in ("PASS", "FAIL"):
            continue
        for metric, matched in (("got_facts", True), ("exact_match", outcome == "PASS")):
            VerdictRepo(session).create(
                team_id=world.acme_team_id,
                result_id=result.id,
                grader="execution_grounded_sql",
                grader_version="v1",
                metric=metric,
                criterion="correctness",
                bool_value=matched,
                value=1.0 if matched else 0.0,
                justification="seeded",
                raw_output=None,
            )
    session.commit()

    row = next(r for r in _matrix(api_client, world, seeded)["rows"] if r["model_id"] == "model-a")

    assert row["got_facts_rate"] == pytest.approx(2 / 3)
    assert row["exact_rate"] == pytest.approx(1 / 3)


def test_rows_graded_before_the_second_metric_read_none_not_zero(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    row = _matrix(api_client, world, seeded)["rows"][0]

    assert row["got_facts_rate"] is None


def test_a_metric_scored_and_never_true_reads_zero_not_none(
    api_client: TestClient, world: _World, seeded: Seeded, session: Session
) -> None:
    """Scored-and-matched-nothing is a measurement; never-scored is not.

    test_rows_graded_before_the_second_metric_read_none_not_zero passes under
    either reading because it seeds no verdicts at all, so the numerator and
    the verdict count are both zero and None is right either way. This is the
    case that separates them: the PASS and the FAIL each carry a verdict and
    each is false, so the metric was read and
    matched nothing -- 0.0. Reporting None says it was never run. (The DEFER
    is left unscored here only to keep the seed minimal -- it is in the
    denominator either way. Nothing stops a DEFER carrying a verdict: the
    composer runs the grader loop before its DEFER branch and says so.)

    Both readings are asserted, not just the tolerant one. Guarding only
    got_facts leaves exact_rate free to be reverted to the numerator gate with
    every test in this file still green -- which is the shape of defect this
    test exists to catch, one metric over.
    """
    from beacon_storage.models.runs import Result
    from beacon_storage.repository.verdicts import VerdictRepo
    from sqlalchemy import select

    results = session.scalars(select(Result).where(Result.team_id == world.acme_team_id)).all()
    for result in results:
        if str(result.outcome) not in ("PASS", "FAIL"):
            continue
        for metric in ("got_facts", "exact_match"):
            VerdictRepo(session).create(
                team_id=world.acme_team_id,
                result_id=result.id,
                grader="execution_grounded_sql",
                grader_version="v1",
                metric=metric,
                criterion="correctness",
                bool_value=False,
                value=0.0,
                justification="seeded",
                raw_output=None,
            )
    session.commit()

    row = next(r for r in _matrix(api_client, world, seeded)["rows"] if r["model_id"] == "model-a")

    assert row["got_facts_rate"] == pytest.approx(0.0)
    assert row["exact_rate"] == pytest.approx(0.0)


def test_a_verdict_on_an_excluded_result_does_not_make_the_row_look_measured(
    api_client: TestClient, world: _World, seeded: Seeded, session: Session
) -> None:
    """The scored count has to describe the denominator it gates.

    ERROR leaves the rate denominator, but its verdicts survive: the composer
    returns whatever earlier graders already emitted, and every one is
    persisted. So a row can hold a got_facts verdict on a result the rate
    excludes. Counting that as evidence the metric was read makes an entirely
    unscored row report 0.0 -- "measured, matched nothing" -- on the strength
    of a verdict attached to a result no rate counts.

    model-b is PASS, PASS, ERROR. Only the ERROR is scored here, and both
    readings are asserted -- guarding one metric leaves the other free to
    regress, which is how the first version of this fix shipped.
    """
    from beacon_storage.models.runs import Result
    from beacon_storage.repository.verdicts import VerdictRepo
    from sqlalchemy import select

    errored = session.scalars(select(Result).where(Result.team_id == world.acme_team_id)).all()
    for result in errored:
        if str(result.outcome) != "ERROR":
            continue
        for metric in ("got_facts", "exact_match"):
            VerdictRepo(session).create(
                team_id=world.acme_team_id,
                result_id=result.id,
                grader="execution_grounded_sql",
                grader_version="v1",
                metric=metric,
                criterion="correctness",
                bool_value=False,
                value=0.0,
                justification="seeded",
                raw_output=None,
            )
    session.commit()

    row = next(r for r in _matrix(api_client, world, seeded)["rows"] if r["model_id"] == "model-b")

    assert row["n_errors"] == 1
    assert row["got_facts_rate"] is None
    assert row["exact_rate"] is None


def test_a_true_verdict_on_an_excluded_result_cannot_push_a_rate_over_100(
    api_client: TestClient, world: _World, seeded: Seeded, session: Session
) -> None:
    """A numerator that counts what the denominator threw out.

    ERROR leaves the rate denominator but keeps its verdicts, so counting
    every true reading against `n_graded` divides three by two. The UI has no
    defence -- it formats whatever arrives -- so the row reads "150%" for a
    system that answered two questions.

    model-b is PASS, PASS, ERROR: all three scored true, denominator 2.
    """
    from beacon_storage.models.runs import Result
    from beacon_storage.repository.verdicts import VerdictRepo
    from sqlalchemy import select

    results = session.scalars(select(Result).where(Result.team_id == world.acme_team_id)).all()
    run_ids = {r.run_id for r in results if str(r.outcome) == "ERROR"}
    for result in results:
        if result.run_id not in run_ids:
            continue
        for metric in ("got_facts", "exact_match"):
            VerdictRepo(session).create(
                team_id=world.acme_team_id,
                result_id=result.id,
                grader="execution_grounded_sql",
                grader_version="v1",
                metric=metric,
                criterion="correctness",
                bool_value=True,
                value=1.0,
                justification="seeded",
                raw_output=None,
            )
    session.commit()

    row = next(r for r in _matrix(api_client, world, seeded)["rows"] if r["model_id"] == "model-b")

    assert row["n_graded"] == 2
    assert row["n_errors"] == 1
    # Two graded results, both true. The ERROR's true verdict is not a third.
    assert row["got_facts_rate"] == pytest.approx(1.0)
    assert row["exact_rate"] == pytest.approx(1.0)


def test_only_the_latest_verdict_version_is_read(
    api_client: TestClient, world: _World, seeded: Seeded, session: Session
) -> None:
    """Grader versions accumulate on a result as history; the matrix must
    read the CURRENT one, not "any version true" and not the first one. An old
    true reading superseded by a false one stays retired, and the reverse
    counts.

    The three readings have to land on three different numbers or the test
    cannot tell them apart. Seeding one result true-then-false against one
    false-then-true is symmetric -- exactly one is true under the latest
    reading AND under the oldest, so 1/3 holds for both and reversing the
    sort passes. Two results going false-then-true against one going
    true-then-false breaks the symmetry: 2/3 latest, 1/3 oldest, 3/3 for any
    version true.

    Three versions, written "v9", "v10", "v2", so that write order and both
    directions of text order pick three DIFFERENT verdicts -- as text
    "v9" > "v2" > "v10", while by write order "v2" is last. Seeded "v1" then
    "v2" all of them agree and sorting by grader_version passes unnoticed,
    which is the one regression _latest_reading names in its own docstring.
    A two-version pair only relocates the blind spot: "v9"/"v10" catches
    grader_version.desc() and lets grader_version.asc() through, because
    "v10" is also the last write.
    """
    from beacon_storage.models.runs import Result, Run
    from beacon_storage.repository.verdicts import VerdictRepo
    from sqlalchemy import select

    # Keyed by outcome, over a select restricted to model-a's live run.
    #
    # What this replaces: indexing an unordered `select(Result)` spanning all
    # three seeded runs -- model-a's PASS/FAIL/DEFER, model-b's two PASSes and
    # an ERROR, the invalidated run's three FAILs. Picking results[0..2] out of
    # that held only while Postgres happened to return insertion order; a pick
    # landing on another run seeds version history the assertion never reads,
    # and leaves a model-a result unscored, so the rate moved to whatever that
    # left -- green or red on storage internals rather than on the behaviour
    # named in the docstring.
    #
    # The filter below makes order irrelevant: one result per outcome, so the
    # three lookups are total functions of the seed. No order_by is needed and
    # adding one would suggest otherwise.
    model_a = {
        str(r.outcome): r
        for r in session.scalars(
            select(Result)
            .join(Run, Run.id == Result.run_id)
            .where(
                Result.team_id == world.acme_team_id,
                Run.model_id == "model-a",
                Run.invalidated_at.is_(None),
            )
        )
    }
    # Readings are (v9, v10, v2), and v2 is the one a correct read returns.
    #   by id desc (correct)   -> v2:  False, True, True  = 2/3
    #   by id asc  (oldest)    -> v9:  True, False, False = 1/3
    #   by version desc        -> v9:  1/3
    #   by version asc         -> v10: True, True, True   = 3/3
    #   any version true       -> 3/3
    for result, readings in (
        (model_a["PASS"], (True, True, False)),
        (model_a["FAIL"], (False, True, True)),
        (model_a["DEFER"], (False, True, True)),
    ):
        for version, value in zip(("v9", "v10", "v2"), readings, strict=True):
            VerdictRepo(session).create(
                team_id=world.acme_team_id,
                result_id=result.id,
                grader="execution_grounded_sql",
                grader_version=version,
                metric="got_facts",
                criterion="correctness",
                bool_value=value,
                value=1.0 if value else 0.0,
                justification="seeded",
                raw_output=None,
            )
    session.commit()

    row = next(r for r in _matrix(api_client, world, seeded)["rows"] if r["model_id"] == "model-a")

    # Two of the three are true at their latest reading. The four orderings
    # enumerated above land elsewhere: 1/3 for the oldest and for
    # version-string descending, 3/3 for version-string ascending and for
    # counting any version true. Not every wrong rule -- ordering by
    # created_at ties, since it is a server_default now() and now() is
    # constant across a transaction, so DISTINCT ON would pick arbitrarily
    # and this test would be flaky rather than red.
    assert row["got_facts_rate"] == pytest.approx(2 / 3)


def test_a_pooled_row_shows_the_spread_it_averages(
    api_client: TestClient, world: _World, seeded: Seeded, session: Session
) -> None:
    """A row over several runs reports one number, and a reader takes it for a
    quantity. It is a mean over repetitions that disagree -- so the row carries
    the lowest and highest per-run rate it pooled. A single run has no spread."""
    from beacon_storage.ids import uuid7
    from beacon_storage.models.runs import Result, Run
    from sqlalchemy import select

    row = next(r for r in _matrix(api_client, world, seeded)["rows"] if r["model_id"] == "model-a")
    assert row["n_runs"] == 1
    assert row["ex_rate_min"] is None, "one measurement has no error bar"

    # A second run of the SAME configuration -- a deliberate replicate -- that
    # answers every item correctly where the first did not.
    original = session.scalar(select(Run).where(Run.model_id == "model-a"))
    assert original is not None
    replicate = Run(
        id=uuid7(),
        team_id=original.team_id,
        solution_id=original.solution_id,
        suite_id=original.suite_id,
        suite=original.suite,
        dataset_version=original.dataset_version,
        mode=original.mode,
        status=original.status,
        pass_idx=1,
        model_id=original.model_id,
        config_label=original.config_label,
        config_digest=original.config_digest,
        config=dict(original.config or {}),
        created_by=original.created_by,
    )
    session.add(replicate)
    session.flush()
    for result in session.scalars(select(Result).where(Result.run_id == original.id)):
        session.add(
            Result(
                id=uuid7(),
                team_id=result.team_id,
                run_id=replicate.id,
                item_id=result.item_id,
                attempt_idx=1,
                output=dict(result.output or {}),
                output_kind=result.output_kind,
                tokens_input=result.tokens_input,
                tokens_output=result.tokens_output,
                runtime_ms=result.runtime_ms,
                status=result.status,
                outcome="PASS" if str(result.outcome) != "DEFER" else result.outcome,
            )
        )
    session.commit()

    row = next(r for r in _matrix(api_client, world, seeded)["rows"] if r["model_id"] == "model-a")

    assert row["n_runs"] == 2
    # The two runs disagree; the row must say so rather than only averaging.
    assert row["ex_rate_min"] == pytest.approx(1 / 3)
    assert row["ex_rate_max"] == pytest.approx(2 / 3)
    assert row["ex_rate_min"] < row["ex_rate"] < row["ex_rate_max"]
