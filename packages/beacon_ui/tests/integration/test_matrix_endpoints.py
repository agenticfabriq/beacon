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
    """The second metric: right data, tolerant shape. A FAIL on exact match
    whose got_facts verdict is true raises got_facts above EX."""
    from beacon_storage.models.runs import Result
    from beacon_storage.repository.verdicts import VerdictRepo
    from sqlalchemy import select

    results = session.scalars(select(Result).where(Result.team_id == world.acme_team_id)).all()
    for result in results:
        if str(result.outcome) not in ("PASS", "FAIL"):
            continue
        VerdictRepo(session).create(
            team_id=world.acme_team_id,
            result_id=result.id,
            grader="execution_grounded_sql",
            grader_version="v1",
            metric="got_facts",
            criterion="correctness",
            # everything that graded PASS or FAIL got the facts in this seed
            bool_value=True,
            value=1.0,
            justification="seeded",
            raw_output=None,
        )
    session.commit()

    row = next(r for r in _matrix(api_client, world, seeded)["rows"] if r["model_id"] == "model-a")

    assert row["ex_rate"] == pytest.approx(1 / 3)
    # PASS + FAIL both carry true got_facts verdicts; the DEFER does not
    assert row["got_facts_rate"] == pytest.approx(2 / 3)


def test_rows_graded_before_the_second_metric_read_none_not_zero(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    row = _matrix(api_client, world, seeded)["rows"][0]

    assert row["got_facts_rate"] is None


def test_a_metric_scored_and_never_true_reads_zero_not_none(
    api_client: TestClient, world: _World, seeded: Seeded, session: Session
) -> None:
    """Scored-and-matched-nothing is a measurement; never-scored is not.

    The row above passes under either reading because it seeds no verdicts at
    all, so the numerator and the verdict count are both zero and None is
    right either way. This is the case that separates them: every gradeable
    result carries a got_facts verdict and every one of them is false. The
    system was measured and matched nothing, which is 0.0 -- reporting None
    tells the reader the metric was never run.
    """
    from beacon_storage.models.runs import Result
    from beacon_storage.repository.verdicts import VerdictRepo
    from sqlalchemy import select

    results = session.scalars(select(Result).where(Result.team_id == world.acme_team_id)).all()
    for result in results:
        if str(result.outcome) not in ("PASS", "FAIL"):
            continue
        VerdictRepo(session).create(
            team_id=world.acme_team_id,
            result_id=result.id,
            grader="execution_grounded_sql",
            grader_version="v1",
            metric="got_facts",
            criterion="correctness",
            bool_value=False,
            value=0.0,
            justification="seeded",
            raw_output=None,
        )
    session.commit()

    row = next(r for r in _matrix(api_client, world, seeded)["rows"] if r["model_id"] == "model-a")

    assert row["got_facts_rate"] == pytest.approx(0.0)


def test_only_the_latest_verdict_version_is_read(
    api_client: TestClient, world: _World, seeded: Seeded, session: Session
) -> None:
    """Grader versions accumulate on a result as history; the matrix must
    read the CURRENT one, not "any version true". An old true reading
    superseded by a false one stays retired -- and the reverse counts."""
    from beacon_storage.models.runs import Result
    from beacon_storage.repository.verdicts import VerdictRepo
    from sqlalchemy import select

    results = [
        r
        for r in session.scalars(select(Result).where(Result.team_id == world.acme_team_id))
        if str(r.outcome) in ("PASS", "FAIL")
    ]
    old_true_now_false, old_false_now_true = results[0], results[1]
    for result, readings in (
        (old_true_now_false, (True, False)),
        (old_false_now_true, (False, True)),
    ):
        for version, value in zip(("v1", "v2"), readings, strict=True):
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

    # Exactly one of the two results is true at its latest reading. Under
    # "any version true" both would count and the rate would be 2/3.
    assert row["got_facts_rate"] == pytest.approx(1 / 3)


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
