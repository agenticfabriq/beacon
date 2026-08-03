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
    chat_to_data_id: UUID
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
        project_id=world.chat_to_data_id,
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
            project_id=world.chat_to_data_id,
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
                project_id=world.chat_to_data_id,
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
        f"/v1/projects/{world.chat_to_data_id}/results-matrix",
        params={"suite_id": seeded.suite_id, **params},
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

    # 2 simple results per run across both valid runs; the invalidated run's
    # three do not appear
    assert body["difficulty_counts"] == {"simple": 4, "challenging": 2}


def test_medians_are_reported(api_client: TestClient, world: _World, seeded: Seeded) -> None:
    row = _matrix(api_client, world, seeded)["rows"][0]

    assert row["median_tokens"] == 200.0
    assert row["median_runtime_ms"] == 1000.0


def test_the_questions_listing_carries_facets_and_tolerance(
    api_client: TestClient, world: _World, seeded: Seeded
) -> None:
    response = api_client.get(
        f"/v1/projects/{world.chat_to_data_id}/suites/{seeded.suite_id}/items",
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
        f"/v1/projects/{world.chat_to_data_id}/suites/{seeded.suite_id}/items",
        params={"difficulty": "challenging"},
        headers={"X-API-Key": world.alice_key},
    )

    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["difficulty"] == "challenging"
    # facets still describe the whole benchmark
    assert body["difficulty_counts"]["simple"] == 2
