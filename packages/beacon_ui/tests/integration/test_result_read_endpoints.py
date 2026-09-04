"""Reading back what was ingested: the drill-down behind a score.

The shape these have to support is one question: open a run, filter to the
wrong answers, pick a question, and see our SQL and result beside gold's. Each
test below is one step of that, plus the distinctions the register says must
survive -- a deferral is not a failure, and an error is not a zero.
"""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID, uuid4

import pytest
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.runs import HarnessMode, ResultStatus, VerdictOutcome
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.verdicts import VerdictRepo
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

SUITE = "read_path_v1"


class _World(Protocol):
    acme_team_id: UUID
    acme_suite_id: UUID
    alice_id: UUID
    alice_key: str
    bob_key: str
    carol_key: str


@dataclass(frozen=True)
class GradedRun:
    """A run whose results cover every outcome the drill-down must distinguish."""

    run_id: str
    item_ids: list[str]
    failing_item_id: str
    deferred_item_id: str


def _sql_evidence(*, passed: bool) -> dict[str, Any]:
    """The shape the execution grader records, as it records it."""
    evidence: dict[str, Any] = {
        "candidate_sql": "SELECT a, b FROM t",
        "gold_sql": "SELECT a FROM t",
        "order_sensitive": False,
        "candidate_row_count": 58,
        "gold_row_count": 58,
        "candidate_columns": ["a", "b"],
        "gold_columns": ["a"],
        "candidate_sample": [[1, "x"], [2, "y"]],
        "gold_sample": [[1], [2]],
    }
    if not passed:
        evidence["mismatch"] = {
            "kind": "column_arity",
            "detail": "row 0 has 2 columns (a, b), gold has 1 (a)",
        }
    return evidence


@pytest.fixture
def graded_run(session: Session, world: _World) -> GradedRun:
    solution = SolutionRepo(session).create(
        team_id=world.acme_team_id,
        solution_id="read-sut",
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
    run = RunRepo(session).create(
        team_id=world.acme_team_id,
        suite_id=suite.id,
        solution_id=solution.id,
        suite=SUITE,
        dataset_version="v1",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        config={},
        created_by=world.alice_id,
    )
    plan = [
        ("simple", VerdictOutcome.PASS, True),
        ("challenging", VerdictOutcome.FAIL, False),
        ("moderate", VerdictOutcome.DEFER, None),
    ]
    item_ids: list[str] = []
    failing = deferred = ""
    for index, (difficulty, outcome, passed) in enumerate(plan):
        item = EvalItemRepo(session).create(
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite=SUITE,
            team_id=world.acme_team_id,
            dataset_version="v1",
            item_input={
                "question": f"question {index}?",
                "db_id": "school",
                "evidence": "a hint",
            },
            gold_answer={"sql": "SELECT a FROM t"},
            item_metadata={"difficulty": difficulty},
            created_by=world.alice_id,
        )
        item_ids.append(str(item.item_id))
        is_defer = outcome is VerdictOutcome.DEFER
        result = ResultRepo(session).create(
            team_id=world.acme_team_id,
            run_id=run.id,
            item_id=str(item.item_id),
            attempt_idx=0,
            output={"sql": "" if is_defer else "SELECT a, b FROM t", "deferred": is_defer},
            output_kind="sql",
            tokens_input=10,
            tokens_output=20,
            runtime_ms=1234,
            status=ResultStatus.COMPLETED,
            outcome=outcome,
            error=None,
        )
        if passed is not None:
            VerdictRepo(session).create(
                team_id=world.acme_team_id,
                result_id=result.id,
                grader="execution_grounded_sql",
                grader_version="v1",
                criterion="correctness",
                metric="exact_match",
                bool_value=passed,
                value=1.0 if passed else 0.0,
                justification="Mismatch (column_arity): row 0 has 2 columns",
                raw_output=_sql_evidence(passed=passed),
            )
        if outcome is VerdictOutcome.FAIL:
            failing = str(item.item_id)
        if is_defer:
            deferred = str(item.item_id)
    session.commit()
    return GradedRun(
        run_id=str(run.id),
        item_ids=item_ids,
        failing_item_id=failing,
        deferred_item_id=deferred,
    )


def _headers(world: _World) -> dict[str, str]:
    return {"X-API-Key": world.alice_key}


def test_a_runs_results_can_be_listed(
    api_client: TestClient, world: _World, graded_run: GradedRun
) -> None:
    response = api_client.get(
        f"/v1/runs/{graded_run.run_id}/results",
        headers=_headers(world),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(graded_run.item_ids)
    assert {row["item_id"] for row in body["results"]} == set(graded_run.item_ids)


def test_every_row_carries_the_question_it_answers(
    api_client: TestClient, world: _World, graded_run: GradedRun
) -> None:
    """A list of UUIDs is not a drill-down."""
    body = api_client.get(
        f"/v1/runs/{graded_run.run_id}/results",
        headers=_headers(world),
    ).json()

    assert all(row["question"] for row in body["results"])


def test_results_can_be_filtered_to_the_wrong_answers(
    api_client: TestClient, world: _World, graded_run: GradedRun
) -> None:
    body = api_client.get(
        f"/v1/runs/{graded_run.run_id}/results",
        params={"outcome": "FAIL"},
        headers=_headers(world),
    ).json()

    assert body["total"] >= 1
    assert {row["outcome"] for row in body["results"]} == {"FAIL"}


def test_results_can_be_filtered_by_difficulty(
    api_client: TestClient, world: _World, graded_run: GradedRun
) -> None:
    """Difficulty was stored on every BIRD item all along and surfaced nowhere."""
    body = api_client.get(
        f"/v1/runs/{graded_run.run_id}/results",
        params={"difficulty": "challenging"},
        headers=_headers(world),
    ).json()

    assert body["total"] >= 1
    assert {row["difficulty"] for row in body["results"]} == {"challenging"}


def test_the_facet_counts_cover_the_run_not_the_filtered_page(
    api_client: TestClient, world: _World, graded_run: GradedRun
) -> None:
    """A filtered view has to show what it is a slice of."""
    body = api_client.get(
        f"/v1/runs/{graded_run.run_id}/results",
        params={"outcome": "FAIL"},
        headers=_headers(world),
    ).json()

    assert sum(body["outcome_counts"].values()) == len(graded_run.item_ids)
    assert sum(body["difficulty_counts"].values()) == len(graded_run.item_ids)


def test_a_deferral_is_listed_as_its_own_outcome(
    api_client: TestClient, world: _World, graded_run: GradedRun
) -> None:
    """Declining to answer must never appear in the list as a wrong answer."""
    body = api_client.get(
        f"/v1/runs/{graded_run.run_id}/results",
        headers=_headers(world),
    ).json()

    assert body["outcome_counts"].get("DEFER") == 1
    deferred = [row for row in body["results"] if row["outcome"] == "DEFER"]
    assert len(deferred) == 1


def test_paging_does_not_change_the_total(
    api_client: TestClient, world: _World, graded_run: GradedRun
) -> None:
    body = api_client.get(
        f"/v1/runs/{graded_run.run_id}/results",
        params={"limit": 1},
        headers=_headers(world),
    ).json()

    assert len(body["results"]) == 1
    assert body["total"] == len(graded_run.item_ids)


def test_an_item_detail_shows_our_answer_beside_the_gold(
    api_client: TestClient, world: _World, graded_run: GradedRun
) -> None:
    """This is the whole request: our SQL and result, gold SQL and result."""
    item_id = graded_run.failing_item_id

    response = api_client.get(
        f"/v1/runs/{graded_run.run_id}/results/{item_id}",
        headers=_headers(world),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["output"]["sql"]
    assert body["gold"]["sql"]
    assert body["question"]
    evidence = body["verdicts"][0]["evidence"]
    assert evidence["candidate_sample"] is not None
    assert evidence["gold_sample"] is not None
    assert evidence["candidate_columns"] and evidence["gold_columns"]


def test_a_failing_item_says_which_dimension_mismatched(
    api_client: TestClient, world: _World, graded_run: GradedRun
) -> None:
    body = api_client.get(
        f"/v1/runs/{graded_run.run_id}/results/{graded_run.failing_item_id}",
        headers=_headers(world),
    ).json()

    mismatch = body["verdicts"][0]["evidence"]["mismatch"]
    assert mismatch["kind"] in {"row_count", "column_arity", "values"}
    assert mismatch["detail"]


def test_a_deferred_item_reads_as_declined_not_wrong(
    api_client: TestClient, world: _World, graded_run: GradedRun
) -> None:
    body = api_client.get(
        f"/v1/runs/{graded_run.run_id}/results/{graded_run.deferred_item_id}",
        headers=_headers(world),
    ).json()

    assert body["deferred"] is True
    assert body["outcome"] == "DEFER"


def test_an_unknown_item_is_a_404(
    api_client: TestClient, world: _World, graded_run: GradedRun
) -> None:
    response = api_client.get(
        f"/v1/runs/{graded_run.run_id}/results/{uuid4()}",
        headers=_headers(world),
    )

    assert response.status_code == 404


def test_results_of_an_unknown_run_are_a_404(api_client: TestClient, world: _World) -> None:
    response = api_client.get(
        f"/v1/runs/{uuid4()}/results",
        headers=_headers(world),
    )

    assert response.status_code == 404


def test_a_run_in_another_team_is_not_readable(
    api_client: TestClient, world: _World, graded_run: GradedRun
) -> None:
    """Run ids are not capabilities: the reader must be in the owning team."""
    response = api_client.get(
        f"/v1/runs/{graded_run.run_id}/results",
        headers={"X-API-Key": world.carol_key},
    )

    assert response.status_code in (403, 404)


def test_a_metrics_readings_come_back_newest_version_first(
    api_client: TestClient, world: _World, graded_run: GradedRun, session: Session
) -> None:
    """Which version the drill-down shows must match which the matrix counts.

    Verdicts are append-only and versioned, so a regrade leaves a result
    holding several readings of one metric. The drill-down picks its got-facts
    reading with a bare `.find()`, and the matrix defines the current reading
    as newest-by-id -- so an unordered query lets the two surfaces disagree
    about what the same result currently reads, with nothing on screen saying
    which version either came from.

    Ordering is by (metric, id DESC), and BOTH halves are load-bearing. A
    global `id DESC` would also reorder the metrics against each other --
    `exact_match` and `got_facts` from one grading run differ only by
    insertion id -- which breaks every caller reading `verdicts[0]`; that is
    not hypothetical, it broke `test_wrong_rows_grade_fail_with_named_mismatch`
    when tried.
    """
    from beacon_storage.models.runs import Result
    from sqlalchemy import select

    item_id = graded_run.failing_item_id
    result = session.scalars(
        select(Result).where(Result.run_id == UUID(graded_run.run_id), Result.item_id == item_id)
    ).one()

    # A later regrade of the SAME metric, at a newer version.
    VerdictRepo(session).create(
        team_id=world.acme_team_id,
        result_id=result.id,
        grader="execution_grounded_sql",
        grader_version="v9",
        criterion="correctness",
        metric="exact_match",
        bool_value=True,
        value=1.0,
        justification="regraded at v9",
        raw_output={},
    )
    # A SECOND metric, inserted last, so dropping `Verdict.metric` from the
    # ordering is observable here. Without it the fixture holds one metric and
    # the grouping assertion below cannot fail -- a global `id DESC` would
    # leave both assertions green.
    VerdictRepo(session).create(
        team_id=world.acme_team_id,
        result_id=result.id,
        grader="execution_grounded_sql",
        grader_version="v9",
        criterion="correctness",
        metric="got_facts",
        bool_value=True,
        value=1.0,
        justification="tolerant reading at v9",
        raw_output={},
    )
    session.commit()

    body = api_client.get(
        f"/v1/runs/{graded_run.run_id}/results/{item_id}", headers=_headers(world)
    ).json()

    exact = [v for v in body["verdicts"] if v["metric"] == "exact_match"]
    assert [v["grader_version"] for v in exact] == ["v9", "v1"], (
        "the newest reading of a metric must come first, or the panel can show "
        "an older version than the matrix counts"
    )
    # Grouping: each metric's readings sit together rather than interleaving by
    # insertion id. `got_facts` was inserted LAST, so a global `id DESC` would
    # put it first and split the two `exact_match` readings around it.
    metrics = [v["metric"] for v in body["verdicts"]]
    assert metrics == sorted(metrics), f"readings must group by metric, got {metrics}"
    assert metrics.count("exact_match") == 2, metrics
