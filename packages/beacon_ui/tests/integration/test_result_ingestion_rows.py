"""Answer-authority ingestion: pushed rows grade against materialized gold.

No benchmark database is configured in these tests -- that is the point. The
push carries the rows the runner's engine returned; the item's gold carries
rows materialized at import; grading is pure comparison.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID  # noqa: TC003

import pytest
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.runs import HarnessMode
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

SUITE = "rows_authority_v1"


class _World(Protocol):
    acme_team_id: UUID
    alice_id: UUID
    alice_key: str


def _seed(session: Session, world: _World) -> tuple[str, str]:
    """A run awaiting results, plus one SQL item with materialized gold."""
    solution = SolutionRepo(session).create(
        team_id=world.acme_team_id,
        solution_id="rows-sut",
        version="0.1",
        owner_team=world.acme_team_id,
        summary="",
        supported_modes=["EVAL"],
        layers=[],
        created_by=world.alice_id,
    )
    item = EvalItemRepo(session).create(
        tier=EvalItemTier.HUMAN_VERIFIED,
        suite=SUITE,
        team_id=world.acme_team_id,
        dataset_version="v1",
        item_input={"question": "monthly totals?"},
        gold_answer={
            "sql": "SELECT month, total FROM sales",
            "columns": ["month", "total"],
            "rows": [["01", 10.0], ["02", 20.0]],
            "row_count": 2,
        },
        item_metadata={},
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
    session.commit()
    return str(run.id), str(item.item_id)


def _push(
    api_client: TestClient,
    world: _World,
    run_id: str,
    item_id: str,
    rows: list[list[Any]],
    **over: Any,
) -> Any:
    output: dict[str, Any] = {
        "sql": "SELECT month, SUM(v) AS total FROM sales GROUP BY month",
        "columns": ["month", "total"],
        "rows": rows,
        "engine": "duckdb",
    }
    output.update(over.pop("output_over", {}))
    body: dict[str, Any] = {
        "item_id": item_id,
        "attempt_idx": 0,
        "output": output,
        "output_kind": "sql",
        "tokens_input": 10,
        "tokens_output": 5,
        "runtime_ms": 100,
    }
    body.update(over)
    return api_client.post(
        f"/v1/runs/{run_id}/results",
        headers={"X-API-Key": world.alice_key},
        json=body,
    )


def test_matching_rows_grade_pass_without_a_benchmark_db(
    api_client: TestClient, world: _World, session: Session
) -> None:
    run_id, item_id = _seed(session, world)

    response = _push(api_client, world, run_id, item_id, [["02", 20.0], ["01", 10.0]])

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "PASS"


def test_wrong_rows_grade_fail_with_named_mismatch(
    api_client: TestClient, world: _World, session: Session
) -> None:
    run_id, item_id = _seed(session, world)

    response = _push(api_client, world, run_id, item_id, [["01", 10.0], ["02", 99.0]])

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "FAIL"

    detail = api_client.get(
        f"/v1/runs/{run_id}/results/{item_id}",
        headers={"X-API-Key": world.alice_key},
    ).json()
    evidence = detail["verdicts"][0]["evidence"]
    assert evidence["mismatch"]["kind"] == "values"
    assert evidence["engine"] == "duckdb"


def test_the_grader_is_result_set_match_and_emits_got_facts(
    api_client: TestClient, world: _World, session: Session
) -> None:
    run_id, item_id = _seed(session, world)
    _push(
        api_client,
        world,
        run_id,
        item_id,
        [["01", 10.0, "extra"], ["02", 20.0, "extra"]],
        output_over={"columns": ["month", "total", "note"]},
    )

    detail = api_client.get(
        f"/v1/runs/{run_id}/results/{item_id}",
        headers={"X-API-Key": world.alice_key},
    ).json()

    graders = {v["grader"] for v in detail["verdicts"]}
    assert graders == {"result_set_match"}
    by_metric = {v.get("metric"): v for v in detail["verdicts"]}
    assert by_metric["exact_match"]["passed"] is False
    assert by_metric["got_facts"]["passed"] is True


def _seed_accepted_results(session: Session, world: _World) -> tuple[str, str]:
    """A run plus an item whose gold is `accepted_results` -- spider2's shape.

    Two of the three live suites store gold this way: `accepted_results` is a
    SET of acceptable result sets, of which BIRD's single `rows`/`columns` is
    the one-element case. `gold_variants` in the grader handles both by
    design; the ingest gate historically checked only for `rows`.
    """
    solution = SolutionRepo(session).create(
        team_id=world.acme_team_id,
        solution_id="accepted-sut",
        version="0.1",
        owner_team=world.acme_team_id,
        summary="",
        supported_modes=["EVAL"],
        layers=[],
        created_by=world.alice_id,
    )
    item = EvalItemRepo(session).create(
        tier=EvalItemTier.HUMAN_VERIFIED,
        suite=SUITE,
        team_id=world.acme_team_id,
        dataset_version="v1",
        item_input={"question": "monthly totals?"},
        gold_answer={
            # No top-level "rows". This is the whole point of the case.
            "sql": "",
            "condition_cols": [],
            "accepted_results": [
                {"columns": ["month", "total"], "rows": [["01", 10.0], ["02", 20.0]]}
            ],
        },
        item_metadata={},
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
    session.commit()
    return str(run.id), str(item.item_id)


def test_gold_as_accepted_results_is_gradeable_not_refused(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """The gate must admit every shape the grader it selects can read.

    `_composer_for` refused unless `gold["rows"]` existed and then handed the
    item to `ResultSetMatchGrader`, whose `applicable` is
    `bool(gold_variants(...))` -- and `gold_variants` reads
    `accepted_results` first. The precondition checked a different field than
    the grader it guarded, so `spider2_lite_local_v1` (135 items) and
    `fs_payments_v1` (24 gradeable) could not accept a SQL push at all while
    carrying gold the selected grader was built to read.
    """
    run_id, item_id = _seed_accepted_results(session, world)

    response = _push(api_client, world, run_id, item_id, [["02", 20.0], ["01", 10.0]])

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "PASS"


def test_an_item_with_no_gold_of_either_shape_is_still_refused(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """Widening the gate must not make it vacuous.

    An empty `accepted_results` is how the five unanswerable fs_payments items
    are stored -- genuinely no gold to compare against -- and a SQL push on one
    must still be refused rather than composed blind.
    """
    run_id, item_id = _seed_accepted_results(session, world)
    from beacon_storage.models.eval_items import EvalItem
    from sqlalchemy import select

    row = session.scalars(select(EvalItem).where(EvalItem.item_id == UUID(item_id))).one()
    row.gold_answer = {"sql": "", "accepted_results": []}
    session.commit()

    response = _push(api_client, world, run_id, item_id, [["01", 10.0]])

    assert response.status_code == 422, response.text
    assert "gold" in response.text.lower()


def test_a_sql_push_without_rows_is_refused(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """A SQL push must carry its rows; grading blind would store a false FAIL."""
    run_id, item_id = _seed(session, world)

    body = {
        "item_id": item_id,
        "attempt_idx": 0,
        "output": {"sql": "SELECT 1"},
        "output_kind": "sql",
        "tokens_input": 10,
        "tokens_output": 5,
        "runtime_ms": 100,
    }
    response = api_client.post(
        f"/v1/runs/{run_id}/results",
        headers={"X-API-Key": world.alice_key},
        json=body,
    )

    assert response.status_code == 422, response.text
    assert "output.rows" in response.json()["detail"]
