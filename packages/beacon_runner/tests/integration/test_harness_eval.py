from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_graders.composer import VerdictComposer
from beacon_graders.graders.dabstep_answer_matcher import DabstepAnswerMatcher
from beacon_runner.dummy_sut import DummySUT
from beacon_runner.errors import HarnessModeNotSupportedError, SutValidationError
from beacon_runner.harness import HarnessRunner
from beacon_runner.registry import SutRegistry
from beacon_runner.types import (
    EvalItem,
    ExecutionResult,
    ExecutionStep,
    SolutionConfig,
    SolutionIdentity,
)
from beacon_storage.db import make_session_factory
from beacon_storage.models.runs import HarnessMode, ResultStatus, RunStatus
from beacon_storage.models.runs import VerdictOutcome as StorageOutcome
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def _items(n: int) -> list[EvalItem]:
    return [
        EvalItem(
            item_id=f"i-{idx}",
            suite="dummy_smoke_v1",
            query={"question": "what is yes?"},
            ground_truth={"answer": "yes"},
            metadata={},
        )
        for idx in range(n)
    ]


def _bootstrap_dummy(
    engine: Engine,
    *,
    user_email: str,
    team_name: str,
    project_name: str,
) -> tuple[UUID, UUID, UUID, UUID]:
    factory = make_session_factory(engine)
    with factory() as session:
        user = UserRepo(session).create(email=user_email, name=user_email.split("@")[0])
        team = TeamRepo(session).create(name=team_name)
        project = ProjectRepo(session).create(
            team_id=team.id, name=project_name, created_by=user.id
        )
        solution = SolutionRepo(session).create(
            team_id=team.id,
            solution_id="dummy",
            version=DummySUT.VERSION,
            owner_team=team.id,
            summary="",
            supported_modes=["EVAL"],
            layers=[],
            created_by=user.id,
        )
        session.commit()
        return team.id, user.id, project.id, solution.id


def test_harness_runs_dummy_sut_end_to_end(engine: Engine) -> None:
    factory = make_session_factory(engine)
    team_id, user_id, project_id, solution_record_id = _bootstrap_dummy(
        engine,
        user_email="h@example.com",
        team_name="harness-team",
        project_name="hp",
    )

    registry = SutRegistry()
    registry.register(DummySUT(owner_team_id=team_id))
    runner = HarnessRunner(
        session_factory=factory,
        registry=registry,
        composer=VerdictComposer(graders=[DabstepAnswerMatcher()]),
        max_workers=4,
    )

    run_id = runner.run_single(
        team_id=team_id,
        project_id=project_id,
        user_id=user_id,
        solution_record_id=solution_record_id,
        items=_items(20),
        config=SolutionConfig(model_id="dummy", prompt_version="v0", layers_enabled={}),
        suite="dummy_smoke_v1",
        dataset_version="v0",
        pass_idx=0,
    )

    with factory() as session:
        run = RunRepo(session).get(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED
        assert run.mode == HarnessMode.EVAL
        assert len(ResultRepo(session).list_for_run(run_id)) == 20


def test_harness_rejects_unsupported_mode(engine: Engine) -> None:
    factory = make_session_factory(engine)
    team_id, user_id, project_id, solution_record_id = _bootstrap_dummy(
        engine,
        user_email="m@example.com",
        team_name="mode-team",
        project_name="mp",
    )
    registry = SutRegistry()
    registry.register(DummySUT(owner_team_id=team_id))
    runner = HarnessRunner(
        session_factory=factory,
        registry=registry,
        composer=VerdictComposer(graders=[]),
    )

    with pytest.raises(HarnessModeNotSupportedError):
        runner.run_single(
            team_id=team_id,
            project_id=project_id,
            user_id=user_id,
            solution_record_id=solution_record_id,
            items=_items(1),
            config=SolutionConfig(model_id="dummy"),
            suite="s",
            dataset_version="v0",
            pass_idx=0,
            mode=HarnessMode.PR_GATE,
        )


def test_harness_runs_pr_gate_using_eval_loop(engine: Engine) -> None:
    factory = make_session_factory(engine)

    class _PrGateSut:
        def identity(self) -> SolutionIdentity:
            return SolutionIdentity(
                solution_id="pr-gate",
                version="0.1",
                owner_team=uuid4(),
                summary="",
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
                    name="root",
                    level="workflow",
                ),
            )

    with factory() as session:
        user = UserRepo(session).create(email="pg@example.com", name="PG")
        team = TeamRepo(session).create(name="pr-gate-team")
        project = ProjectRepo(session).create(team_id=team.id, name="pgp", created_by=user.id)
        solution = SolutionRepo(session).create(
            team_id=team.id,
            solution_id="pr-gate",
            version="0.1",
            owner_team=team.id,
            summary="",
            supported_modes=["PR_GATE"],
            layers=[],
            created_by=user.id,
        )
        session.commit()
        team_id, user_id, project_id, solution_record_id = (
            team.id,
            user.id,
            project.id,
            solution.id,
        )

    registry = SutRegistry()
    registry.register(_PrGateSut())
    runner = HarnessRunner(
        session_factory=factory,
        registry=registry,
        composer=VerdictComposer(graders=[DabstepAnswerMatcher()]),
    )

    run_id = runner.run_single(
        team_id=team_id,
        project_id=project_id,
        user_id=user_id,
        solution_record_id=solution_record_id,
        items=_items(2),
        config=SolutionConfig(model_id="pr-gate"),
        suite="s",
        dataset_version="v0",
        pass_idx=2,
        mode=HarnessMode.PR_GATE,
    )

    with factory() as session:
        run = RunRepo(session).get(run_id)
        assert run is not None
        assert run.mode == HarnessMode.PR_GATE
        assert run.status == RunStatus.COMPLETED
        results = ResultRepo(session).list_for_run(run_id)
        assert len(results) == 2
        assert {result.attempt_idx for result in results} == {2}


def test_harness_marks_failed_when_validate_config_returns_errors(engine: Engine) -> None:
    factory = make_session_factory(engine)
    team_id, user_id, project_id, solution_record_id = _bootstrap_dummy(
        engine,
        user_email="v@example.com",
        team_name="val-team",
        project_name="vp",
    )
    registry = SutRegistry()
    registry.register(DummySUT(owner_team_id=team_id))
    runner = HarnessRunner(
        session_factory=factory,
        registry=registry,
        composer=VerdictComposer(graders=[]),
    )
    bad_config = SolutionConfig(
        model_id="dummy",
        prompt_version="v0",
        layers_enabled={"bogus": False},
    )

    with pytest.raises(SutValidationError):
        runner.run_single(
            team_id=team_id,
            project_id=project_id,
            user_id=user_id,
            solution_record_id=solution_record_id,
            items=_items(1),
            config=bad_config,
            suite="s",
            dataset_version="v0",
            pass_idx=0,
        )


def test_harness_continues_after_individual_item_error(engine: Engine) -> None:
    factory = make_session_factory(engine)

    class _FlakySut:
        def identity(self) -> SolutionIdentity:
            return SolutionIdentity(
                solution_id="flaky",
                version="0.1",
                owner_team=uuid4(),
                summary="",
                supported_modes=["EVAL"],
                layers=[],
            )

        def layers(self) -> list[object]:
            return []

        def validate_config(self, config: SolutionConfig) -> list[str]:  # noqa: ARG002
            return []

        def invoke(self, item: EvalItem, config: SolutionConfig) -> ExecutionResult:  # noqa: ARG002
            if item.item_id == "i-1":
                raise RuntimeError("simulated crash")
            return ExecutionResult(
                output={"answer": "yes"},
                output_kind="answer",
                trace=ExecutionStep(uuid="r", name="r", level="workflow", status="COMPLETED"),
            )

    with factory() as session:
        user = UserRepo(session).create(email="f@example.com", name="F")
        team = TeamRepo(session).create(name="flaky-team")
        project = ProjectRepo(session).create(team_id=team.id, name="fp", created_by=user.id)
        solution = SolutionRepo(session).create(
            team_id=team.id,
            solution_id="flaky",
            version="0.1",
            owner_team=team.id,
            summary="",
            supported_modes=["EVAL"],
            layers=[],
            created_by=user.id,
        )
        session.commit()
        team_id, user_id, project_id, solution_record_id = (
            team.id,
            user.id,
            project.id,
            solution.id,
        )
    registry = SutRegistry()
    registry.register(_FlakySut())
    runner = HarnessRunner(
        session_factory=factory,
        registry=registry,
        composer=VerdictComposer(graders=[DabstepAnswerMatcher()]),
        max_workers=2,
    )

    run_id = runner.run_single(
        team_id=team_id,
        project_id=project_id,
        user_id=user_id,
        solution_record_id=solution_record_id,
        items=_items(3),
        config=SolutionConfig(model_id="dummy"),
        suite="s",
        dataset_version="v0",
        pass_idx=0,
    )

    with factory() as session:
        run = RunRepo(session).get(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED
        results = ResultRepo(session).list_for_run(run_id)
        errors = [row for row in results if row.status == ResultStatus.ERROR]
        assert len(errors) == 1
        assert errors[0].outcome == StorageOutcome.ERROR
