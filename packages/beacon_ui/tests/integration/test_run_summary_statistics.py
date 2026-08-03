"""The /v1/suites/{id}/runs summary reports real pass@k, not a placeholder (B3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import UUID  # noqa: TC003

import pytest
from beacon_storage.models.runs import HarnessMode, ResultStatus, VerdictOutcome
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

_OUTCOMES = {
    "PASS": VerdictOutcome.PASS,
    "FAIL": VerdictOutcome.FAIL,
    "ERROR": VerdictOutcome.ERROR,
}


class _World(Protocol):
    acme_team_id: UUID
    acme_suite_id: UUID
    alice_id: UUID
    alice_key: str


def _seed_run(
    session: Session,
    world: _World,
    *,
    suite: str,
    attempts: dict[str, list[str]],
) -> UUID:
    """Create one completed run whose items have the given per-attempt outcomes."""
    solution = SolutionRepo(session).get_by_team_and_solution(
        world.acme_team_id, "summary-sut", "0.1"
    ) or SolutionRepo(session).create(
        team_id=world.acme_team_id,
        solution_id="summary-sut",
        version="0.1",
        owner_team=world.acme_team_id,
        summary="Run summary fixture",
        supported_modes=["EVAL"],
        layers=[],
        created_by=world.alice_id,
    )
    session.flush()

    run_repo = RunRepo(session)
    run = run_repo.create(
        team_id=world.acme_team_id,
        suite_id=world.acme_suite_id,
        solution_id=solution.id,
        suite=suite,
        dataset_version="v2",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        config={},
        created_by=world.alice_id,
    )
    result_repo = ResultRepo(session)
    for item_id, outcomes in attempts.items():
        for attempt_idx, outcome in enumerate(outcomes):
            result_repo.create(
                team_id=world.acme_team_id,
                run_id=run.id,
                item_id=item_id,
                attempt_idx=attempt_idx,
                output={"answer": "x"},
                output_kind="answer",
                tokens_input=10,
                tokens_output=10,
                runtime_ms=100,
                status=ResultStatus.COMPLETED,
                outcome=_OUTCOMES[outcome],
                error=None,
            )
    run_repo.mark_completed(run.id)
    session.commit()
    return run.id


def _summary_for(
    api_client: TestClient,
    world: _World,
    run_id: UUID,
) -> dict[str, object]:
    response = api_client.get(
        f"/v1/suites/{world.acme_suite_id}/runs",
        headers={"X-API-Key": world.alice_key},
    )
    assert response.status_code == 200, response.text
    runs = [row for row in response.json() if row["run_id"] == str(run_id)]
    assert len(runs) == 1, f"run {run_id} not in listing"
    return dict(runs[0]["summary"])


def test_single_attempt_run_reports_pass_at_1_only(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    """One attempt per item cannot answer pass@3/@5 or pass^3 -- they must be null."""
    run_id = _seed_run(
        session,
        world,
        suite="summary_single_v1",
        attempts={"i-0": ["PASS"], "i-1": ["PASS"], "i-2": ["FAIL"], "i-3": ["FAIL"]},
    )

    summary = _summary_for(api_client, world, run_id)

    assert summary["pass_at_1"] == pytest.approx(0.5)
    # Previously all four fields carried the pass@1 number.
    assert summary["pass_at_3"] is None
    assert summary["pass_at_5"] is None
    assert summary["pass_hat_3"] is None
    assert summary["n_items"] == 4
    assert summary["n_errors"] == 0


def test_infra_errors_leave_the_denominator_and_are_counted(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    """An outage must read as n_errors, not as a depressed pass rate (B3)."""
    run_id = _seed_run(
        session,
        world,
        suite="summary_errors_v1",
        attempts={
            "i-0": ["PASS"],
            "i-1": ["FAIL"],
            "i-2": ["ERROR"],
            "i-3": ["ERROR"],
        },
    )

    summary = _summary_for(api_client, world, run_id)

    # Counting the two errors as failures would report 0.25.
    assert summary["pass_at_1"] == pytest.approx(0.5)
    assert summary["n_items"] == 4
    assert summary["n_errors"] == 2


def test_an_item_survives_an_error_on_an_earlier_attempt(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    """A retried item is graded on the attempt that actually ran."""
    run_id = _seed_run(
        session,
        world,
        suite="summary_retry_v1",
        attempts={"i-0": ["ERROR", "PASS"], "i-1": ["FAIL", "FAIL"]},
    )

    summary = _summary_for(api_client, world, run_id)

    assert summary["pass_at_1"] == pytest.approx(0.5)
    assert summary["n_items"] == 2
    assert summary["n_errors"] == 0


def test_three_attempt_run_reports_real_pass_at_3_and_pass_hat_3(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    """With three attempts per item, pass@3 and pass^3 are answerable and differ."""
    run_id = _seed_run(
        session,
        world,
        suite="summary_triple_v1",
        attempts={
            "i-0": ["PASS", "PASS", "PASS"],
            "i-1": ["FAIL", "FAIL", "PASS"],
            "i-2": ["FAIL", "FAIL", "FAIL"],
            "i-3": ["FAIL", "FAIL", "FAIL"],
        },
    )

    summary = _summary_for(api_client, world, run_id)

    # pass@1: only i-0 passes first attempt. pass@3: i-0 and i-1 pass within 3.
    assert summary["pass_at_1"] == pytest.approx(0.25)
    assert summary["pass_at_3"] == pytest.approx(0.5)
    # pass^3 needs all three attempts to pass: only i-0.
    assert summary["pass_hat_3"] == pytest.approx(0.25)
    # Five attempts are still unavailable.
    assert summary["pass_at_5"] is None
