"""GateService PR_GATE orchestration tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from beacon_graders.composer import VerdictComposer
from beacon_graders.graders.dabstep_answer_matcher import DabstepAnswerMatcher
from beacon_runner.registry import SutRegistry
from beacon_runner.service.gate import BaselineRunNotConfigured, GateService
from beacon_runner.types import (
    EvalItem,
    ExecutionResult,
    ExecutionStep,
    SolutionConfig,
    SolutionIdentity,
)
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.runs import HarnessMode, ResultStatus
from beacon_storage.models.runs import VerdictOutcome as StorageOutcome
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class _GateWorld:
    session: Session
    registry: SutRegistry
    project_id: UUID
    new_sut_id: UUID
    curated_suite_id: UUID
    baseline_run_id: UUID | None


class _OutcomeSut:
    version = "0.1"

    def __init__(
        self,
        *,
        owner_team_id: UUID,
        solution_id: str,
        outcomes: dict[str, Sequence[bool]],
    ) -> None:
        self._owner_team_id = owner_team_id
        self._solution_id = solution_id
        self._outcomes = outcomes

    def identity(self) -> SolutionIdentity:
        return SolutionIdentity(
            solution_id=self._solution_id,
            version=self.version,
            owner_team=self._owner_team_id,
            summary="deterministic PR gate SUT",
            supported_modes=["PR_GATE"],
            layers=[],
        )

    def layers(self) -> list[object]:
        return []

    def validate_config(self, config: SolutionConfig) -> list[str]:  # noqa: ARG002
        return []

    def invoke(self, item: EvalItem, config: SolutionConfig) -> ExecutionResult:
        pass_idx = int(config.extras["gate_pass_idx"])
        passed = self._outcomes[item.item_id][pass_idx]
        return ExecutionResult(
            output={"answer": "yes" if passed else "no"},
            output_kind="answer",
            trace=ExecutionStep(
                uuid=f"root-{item.item_id}-{pass_idx}",
                name="gate_test",
                level="workflow",
            ),
        )


def _world(
    session: Session,
    *,
    slug: str,
    baseline: Sequence[Sequence[bool]],
    candidate: Sequence[Sequence[bool]],
    set_project_baseline: bool = True,
) -> _GateWorld:
    user = UserRepo(session).create(email=f"{slug}@example.com", name=slug)
    team = TeamRepo(session).create(name=f"{slug}-team")
    project = ProjectRepo(session).create(
        team_id=team.id,
        name=f"{slug}-project",
        created_by=user.id,
    )
    suite = SuiteRepo(session).create(
        project_id=project.id,
        team_id=team.id,
        name=f"{slug}-curated-50",
        description="curated subset for PR gate",
        method="separability_gain",
        suite_metadata={"subset_tag": f"curated_50_{slug}"},
        created_by=user.id,
    )
    baseline_solution = SolutionRepo(session).create(
        team_id=team.id,
        solution_id=f"{slug}-baseline",
        version="0.1",
        owner_team=team.id,
        summary="",
        supported_modes=["PR_GATE"],
        layers=[],
        created_by=user.id,
    )
    candidate_solution_id = f"{slug}-candidate"
    candidate_solution = SolutionRepo(session).create(
        team_id=team.id,
        solution_id=candidate_solution_id,
        version="0.1",
        owner_team=team.id,
        summary="",
        supported_modes=["PR_GATE"],
        layers=[],
        created_by=user.id,
    )

    item_ids: list[UUID] = []
    for idx, attempts in enumerate(baseline):
        if len(attempts) != 3:
            raise AssertionError("baseline attempts must have K=3")
        item = EvalItemRepo(session).insert_new_version(
            item_id=uuid4(),
            valid_from=datetime.now(UTC) + timedelta(microseconds=idx),
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite=suite.name,
            team_id=team.id,
            solution_id=None,
            dataset_version="v0",
            item_input={"question": f"item {idx}"},
            gold_answer={"answer": "yes"},
            item_metadata={},
            created_by=user.id,
        )
        item_ids.append(item.item_id)

    SuiteRepo(session).add_items(suite_id=suite.id, item_ids=item_ids)
    baseline_run_id = _seed_baseline_runs(
        session,
        team_id=team.id,
        project_id=project.id,
        user_id=user.id,
        solution_id=baseline_solution.id,
        suite=suite.name,
        item_ids=item_ids,
        attempts=baseline,
    )
    if set_project_baseline:
        project.baseline_run_id = baseline_run_id

    registry = SutRegistry()
    registry.register(
        _OutcomeSut(
            owner_team_id=team.id,
            solution_id=candidate_solution_id,
            outcomes={
                str(item_id): tuple(attempts)
                for item_id, attempts in zip(item_ids, candidate, strict=True)
            },
        )
    )
    session.commit()
    return _GateWorld(
        session=session,
        registry=registry,
        project_id=project.id,
        new_sut_id=candidate_solution.id,
        curated_suite_id=suite.id,
        baseline_run_id=baseline_run_id if set_project_baseline else None,
    )


def _seed_baseline_runs(
    session: Session,
    *,
    team_id: UUID,
    project_id: UUID,
    user_id: UUID,
    solution_id: UUID,
    suite: str,
    item_ids: Sequence[UUID],
    attempts: Sequence[Sequence[bool]],
) -> UUID:
    group_id = uuid4()
    baseline_run_id: UUID | None = None
    for pass_idx in range(3):
        run = RunRepo(session).create(
            team_id=team_id,
            project_id=project_id,
            solution_id=solution_id,
            suite=suite,
            dataset_version="v0",
            mode=HarnessMode.PR_GATE,
            pass_idx=pass_idx,
            parent_sweep_id=group_id,
            config={},
            created_by=user_id,
        )
        if pass_idx == 0:
            baseline_run_id = run.id
        RunRepo(session).mark_completed(run.id)
        for item_id, item_attempts in zip(item_ids, attempts, strict=True):
            passed = item_attempts[pass_idx]
            ResultRepo(session).create(
                team_id=team_id,
                project_id=project_id,
                run_id=run.id,
                item_id=str(item_id),
                attempt_idx=pass_idx,
                output={"answer": "yes" if passed else "no"},
                output_kind="answer",
                tokens_input=0,
                tokens_output=0,
                runtime_ms=0,
                status=ResultStatus.COMPLETED,
                outcome=StorageOutcome.PASS if passed else StorageOutcome.FAIL,
                error=None,
            )
    assert baseline_run_id is not None
    return baseline_run_id


def test_gate_passes_when_new_matches_baseline_within_band(session: Session) -> None:
    world = _world(
        session,
        slug="gate-pass",
        baseline=[(True, True, True)] * 8 + [(False, False, False)] * 2,
        candidate=[(True, True, True)] * 8 + [(False, False, False)] * 2,
    )
    svc = GateService(
        world.session,
        registry=world.registry,
        composer=VerdictComposer(graders=[DabstepAnswerMatcher()]),
    )

    res = svc.run_gate(
        project_id=world.project_id,
        sut_id=world.new_sut_id,
        suite_id=world.curated_suite_id,
        commit_sha="abc123",
        baseline_run_id=world.baseline_run_id,
    )

    assert res.outcome == "PASS"
    assert res.delta_pass_at_3 == pytest.approx(0.0)
    assert res.delta_pass_hat_3 == pytest.approx(0.0)


def test_gate_fails_when_pass_hat_3_drops_more_than_2_points(session: Session) -> None:
    world = _world(
        session,
        slug="gate-hat-drop",
        baseline=[(True, True, True)] * 8 + [(False, False, False)] * 2,
        candidate=[(True, True, False)] * 8 + [(False, False, False)] * 2,
    )
    svc = GateService(
        world.session,
        registry=world.registry,
        composer=VerdictComposer(graders=[DabstepAnswerMatcher()]),
    )

    res = svc.run_gate(
        project_id=world.project_id,
        sut_id=world.new_sut_id,
        suite_id=world.curated_suite_id,
        commit_sha="abc124",
        baseline_run_id=world.baseline_run_id,
    )

    assert res.outcome == "FAIL"
    assert res.delta_pass_at_3 == pytest.approx(0.0)
    assert res.delta_pass_hat_3 < -0.02
    assert "pass^3" in res.reason


def test_gate_fails_on_mcnemar_with_negative_delta(session: Session) -> None:
    world = _world(
        session,
        slug="gate-mcnemar",
        baseline=[(True, False, False)] * 9 + [(False, False, False)] * 3,
        candidate=[(False, False, False)] * 8
        + [(True, False, False)]
        + [(True, False, False)]
        + [(False, False, False)] * 2,
    )
    svc = GateService(
        world.session,
        registry=world.registry,
        composer=VerdictComposer(graders=[DabstepAnswerMatcher()]),
    )

    res = svc.run_gate(
        project_id=world.project_id,
        sut_id=world.new_sut_id,
        suite_id=world.curated_suite_id,
        commit_sha="abc125",
        baseline_run_id=world.baseline_run_id,
    )

    assert res.outcome == "FAIL"
    assert res.bh_adjusted_p < 0.05
    assert res.delta_pass_at_3 < 0


def test_gate_raises_when_no_baseline_set(session: Session) -> None:
    world = _world(
        session,
        slug="gate-no-baseline",
        baseline=[(True, True, True)],
        candidate=[(True, True, True)],
        set_project_baseline=False,
    )
    svc = GateService(
        world.session,
        registry=world.registry,
        composer=VerdictComposer(graders=[DabstepAnswerMatcher()]),
    )

    with pytest.raises(BaselineRunNotConfigured):
        svc.run_gate(
            project_id=world.project_id,
            sut_id=world.new_sut_id,
            suite_id=world.curated_suite_id,
            commit_sha="abc126",
            baseline_run_id=None,
        )


def test_gate_refuses_to_judge_when_there_is_nothing_to_compare(session: Session) -> None:
    """A gate with no gradeable results must not approve (B15).

    The suite rates used to come back as 0.0 for an empty task set, making both
    deltas 0.0 and every block condition false -- so the gate passed a change on
    zero evaluation data.
    """
    world = _world(session, slug="gate-empty", baseline=[], candidate=[])
    svc = GateService(
        world.session,
        registry=world.registry,
        composer=VerdictComposer(graders=[DabstepAnswerMatcher()]),
    )

    res = svc.run_gate(
        project_id=world.project_id,
        sut_id=world.new_sut_id,
        suite_id=world.curated_suite_id,
        commit_sha="empty123",
        baseline_run_id=world.baseline_run_id,
    )

    assert res.outcome == "FAIL"
    assert "refusing to judge" in res.reason
