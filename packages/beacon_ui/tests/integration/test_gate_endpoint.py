from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID  # noqa: TC003

import pytest
from beacon_runner.registry import default_registry
from beacon_runner.types import (
    EvalItem,
    ExecutionResult,
    ExecutionStep,
    SolutionConfig,
    SolutionIdentity,
)
from beacon_storage.ids import uuid7
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.runs import HarnessMode, ResultStatus
from beacon_storage.models.runs import VerdictOutcome as StorageOutcome
from beacon_storage.models.solutions import Solution  # noqa: TC002
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from fastapi.testclient import TestClient  # noqa: TC002
from sqlalchemy.orm import Session  # noqa: TC002

pytestmark = pytest.mark.integration


class _World(Protocol):
    acme_team_id: UUID
    chat_to_data_id: UUID
    acme_solution_id: UUID
    alice_id: UUID
    alice_key: str
    carol_key: str


@dataclass(frozen=True)
class _GateFixture:
    solution: Solution
    suite_id: UUID


class _PassingGateSut:
    version = "0.1"

    def __init__(self, *, owner_team_id: UUID, solution_id: str) -> None:
        self._owner_team_id = owner_team_id
        self._solution_id = solution_id

    def identity(self) -> SolutionIdentity:
        return SolutionIdentity(
            solution_id=self._solution_id,
            version=self.version,
            owner_team=self._owner_team_id,
            summary="API gate test SUT",
            supported_modes=["PR_GATE"],
            layers=[],
        )

    def layers(self) -> list[object]:
        return []

    def validate_config(self, config: SolutionConfig) -> list[str]:  # noqa: ARG002
        return []

    def invoke(self, item: EvalItem, config: SolutionConfig) -> ExecutionResult:  # noqa: ARG002
        return ExecutionResult(
            output={"answer": "yes"},
            output_kind="answer",
            trace=ExecutionStep(
                uuid=f"root-{item.item_id}",
                name="gate_api_test",
                level="workflow",
            ),
        )


def _setup_gate_fixture(session: Session, world: _World) -> _GateFixture:
    suite_repo = SuiteRepo(session)
    suite = suite_repo.create(
        project_id=world.chat_to_data_id,
        team_id=world.acme_team_id,
        name="curated_50_api",
        description="API gate curated subset",
        method="separability_gain",
        suite_metadata={"subset_tag": "curated_50_api"},
        created_by=world.alice_id,
    )
    item_ids = [
        EvalItemRepo(session)
        .create(
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite=suite.name,
            team_id=world.acme_team_id,
            dataset_version="v0",
            item_input={"question": f"gate item {idx}"},
            gold_answer={"answer": "yes"},
            item_metadata={"source": "task-9-test"},
            created_by=world.alice_id,
        )
        .item_id
        for idx in range(3)
    ]
    suite_repo.add_items(suite_id=suite.id, item_ids=item_ids)
    baseline_run_id = _seed_baseline_runs(
        session,
        world=world,
        suite_name=suite.name,
        item_ids=item_ids,
    )
    project = ProjectRepo(session).get(world.chat_to_data_id)
    assert project is not None
    project.baseline_run_id = baseline_run_id

    solution_id = f"api-gate-sut-{uuid7()}"
    solution = SolutionRepo(session).create(
        team_id=world.acme_team_id,
        solution_id=solution_id,
        version="0.1",
        owner_team=world.acme_team_id,
        summary="API gate SUT",
        supported_modes=["PR_GATE"],
        layers=[],
        created_by=world.alice_id,
    )
    default_registry().register(
        _PassingGateSut(owner_team_id=world.acme_team_id, solution_id=solution_id)
    )
    session.commit()
    return _GateFixture(solution=solution, suite_id=suite.id)


def _seed_baseline_runs(
    session: Session,
    *,
    world: _World,
    suite_name: str,
    item_ids: list[UUID],
) -> UUID:
    group_id = uuid7()
    baseline_run_id: UUID | None = None
    for pass_idx in range(3):
        run = RunRepo(session).create(
            team_id=world.acme_team_id,
            project_id=world.chat_to_data_id,
            solution_id=world.acme_solution_id,
            suite=suite_name,
            dataset_version="v0",
            mode=HarnessMode.PR_GATE,
            pass_idx=pass_idx,
            parent_sweep_id=group_id,
            config={},
            created_by=world.alice_id,
        )
        if pass_idx == 0:
            baseline_run_id = run.id
        RunRepo(session).mark_completed(run.id)
        for item_id in item_ids:
            ResultRepo(session).create(
                team_id=world.acme_team_id,
                project_id=world.chat_to_data_id,
                run_id=run.id,
                item_id=str(item_id),
                attempt_idx=pass_idx,
                output={"answer": "yes"},
                output_kind="answer",
                tokens_input=0,
                tokens_output=0,
                runtime_ms=0,
                status=ResultStatus.COMPLETED,
                outcome=StorageOutcome.PASS,
                error=None,
            )
    assert baseline_run_id is not None
    return baseline_run_id


def test_gate_webhook_returns_decision(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    fixture = _setup_gate_fixture(session, world)
    attach_response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/solutions",
        headers={"X-API-Key": world.alice_key},
        json={"solution_id": str(fixture.solution.id)},
    )
    assert attach_response.status_code == 201, attach_response.text

    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/gate",
        headers={"X-API-Key": world.alice_key},
        json={
            "solution_id": str(fixture.solution.id),
            "commit_sha": "deadbeef" * 5,
            "ci_run_url": "https://ci.example.com/job/1",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] in ("pass", "fail", "warn")
    assert body["run_id"]
    assert body["baseline_run_id"]
    assert "pass_at_3" in body["delta"]


def test_gate_requires_run_eval_permission(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    fixture = _setup_gate_fixture(session, world)

    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/gate",
        headers={"X-API-Key": world.carol_key},
        json={"solution_id": str(fixture.solution.id), "commit_sha": "deadbeef"},
    )

    assert response.status_code == 403
