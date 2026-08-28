"""Cost that was never measured must not arrive on screen as zero.

Three of beacon's readings already refuse to turn an absence into a number: an
ERROR leaves the pass-rate denominator, a rate over nothing gradeable is None,
and a failed matrix load says so rather than rendering an empty table. Tokens
were the exception -- the column is fed by importers that had no per-item cost
to give and wrote 0, which the matrix medians into 0 and the page prints as a
configuration that costs nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID  # noqa: TC003

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

SUITE = "unmeasured_cost_v1"


class _World(Protocol):
    acme_team_id: UUID
    alice_id: UUID
    alice_key: str


def _seed(session: Session, world: _World, *, tokens: tuple[int | None, int | None]) -> str:
    """One run of two items whose per-item cost is ``tokens``. Returns suite id."""
    solution = SolutionRepo(session).create(
        team_id=world.acme_team_id,
        solution_id="cost-sut",
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
        sweep_arm="imported",
        config={"model_id": "model-x"},
        model_id="model-x",
        config_label="baseline",
        config_digest="digest-x",
        created_by=world.alice_id,
    )
    for index in range(2):
        item = EvalItemRepo(session).create(
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite=SUITE,
            team_id=world.acme_team_id,
            dataset_version="v1",
            item_input={"question": f"q{index}?", "db_id": "db"},
            gold_answer={"sql": "SELECT 1"},
            item_metadata={"difficulty": "simple", "source": "test-corpus"},
            created_by=world.alice_id,
        )
        ResultRepo(session).create(
            team_id=world.acme_team_id,
            run_id=run.id,
            item_id=str(item.item_id),
            attempt_idx=0,
            output={"sql": "SELECT 1"},
            output_kind="sql",
            tokens_input=tokens[0],
            tokens_output=tokens[1],
            runtime_ms=1000,
            status=ResultStatus.COMPLETED,
            outcome=VerdictOutcome.PASS,
            error=None,
        )
    session.commit()
    return str(suite.id)


def _row(api_client: TestClient, world: _World, suite_id: str) -> dict[str, Any]:
    response = api_client.get(
        f"/v1/suites/{suite_id}/results-matrix",
        headers={"X-API-Key": world.alice_key},
    )
    assert response.status_code == 200, response.text
    rows: list[dict[str, Any]] = response.json()["rows"]
    assert len(rows) == 1
    return rows[0]


def test_a_run_that_recorded_no_tokens_reports_no_median(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """The defect: both file importers had no per-item cost and wrote 0."""
    suite_id = _seed(session, world, tokens=(None, None))

    assert _row(api_client, world, suite_id)["median_tokens"] is None


def test_unrecorded_cost_does_not_cost_the_row_its_latency(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """Tokens and latency are measured separately; one absent is not both."""
    suite_id = _seed(session, world, tokens=(None, None))

    assert _row(api_client, world, suite_id)["median_runtime_ms"] == 1000.0


def test_a_run_that_did_record_tokens_still_reports_them(
    api_client: TestClient, world: _World, session: Session
) -> None:
    suite_id = _seed(session, world, tokens=(100, 40))

    assert _row(api_client, world, suite_id)["median_tokens"] == 140.0


def test_a_measured_zero_is_still_a_measurement(
    api_client: TestClient, world: _World, session: Session
) -> None:
    """None means unrecorded. A runner that truly reports 0 keeps its 0."""
    suite_id = _seed(session, world, tokens=(0, 0))

    assert _row(api_client, world, suite_id)["median_tokens"] == 0.0
