from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_graders.types import Verdict as GraderVerdict
from beacon_graders.types import VerdictOutcome as GraderOutcome
from beacon_runner.persistence import persist_result
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep
from beacon_storage.models.runs import HarnessMode, ResultStatus
from beacon_storage.models.runs import VerdictOutcome as StorageOutcome
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.traces import TraceRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.repository.verdicts import VerdictRepo
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from beacon_storage.models import Run, Solution, Team, User
    from beacon_storage.models.suites import Suite
    from sqlalchemy.orm import Session

    _Ctx = tuple[Team, User, Suite, Solution, Run]

pytestmark = pytest.mark.integration


@pytest.fixture
def _ctx(session: Session) -> _Ctx:
    user = UserRepo(session).create(email="p@example.com", name="P")
    team = TeamRepo(session).create(name="pers-team")
    project = SuiteRepo(session).create(
        team_id=team.id,
        name="pp",
        description="",
        method="manual",
        suite_metadata={},
        created_by=user.id,
    )
    solution = SolutionRepo(session).create(
        team_id=team.id,
        solution_id="dummy",
        version="0.2.0",
        owner_team=team.id,
        summary="",
        supported_modes=["EVAL"],
        layers=[],
        created_by=user.id,
    )
    run = RunRepo(session).create(
        team_id=team.id,
        suite_id=project.id,
        solution_id=solution.id,
        suite="s",
        dataset_version="v0",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        config={},
        created_by=user.id,
    )
    session.commit()
    return team, user, project, solution, run


def test_persist_result_writes_result_verdicts_and_trace(session: Session, _ctx: _Ctx) -> None:
    team, _user, project, _solution, run = _ctx
    item = EvalItem(
        item_id="i-1",
        suite="s",
        query={"q": "?"},
        ground_truth={"answer": "yes"},
        metadata={},
    )
    exec_result = ExecutionResult(
        output={"answer": "yes"},
        output_kind="answer",
        trace=ExecutionStep(uuid="root", name="root", level="workflow", status="COMPLETED"),
        tokens_input=5,
        tokens_output=7,
        runtime_ms=12,
    )
    verdicts = [
        GraderVerdict(
            grader="dabstep_answer_matcher",
            grader_version="v1",
            criterion="factoid_match",
            bool_value=True,
            value=1.0,
            justification="match",
            raw_output={"x": 1},
        )
    ]

    persist_result(
        session,
        team_id=team.id,
        run_id=run.id,
        item=item,
        attempt_idx=0,
        exec_result=exec_result,
        verdicts=verdicts,
        outcome=GraderOutcome.PASS,
    )
    session.commit()

    persisted = ResultRepo(session).list_for_run(run.id)
    assert len(persisted) == 1
    result_row = persisted[0]
    assert result_row.item_id == "i-1"
    assert result_row.outcome == StorageOutcome.PASS
    assert result_row.status == ResultStatus.COMPLETED

    stored_verdicts = VerdictRepo(session).list_for_result(result_row.id)
    assert len(stored_verdicts) == 1
    assert stored_verdicts[0].grader == "dabstep_answer_matcher"

    trace = TraceRepo(session).get_for_result(result_row.id)
    assert trace is not None
    assert trace.step_tree["name"] == "root"


def test_persist_result_marks_error_status_when_outcome_is_error(
    session: Session, _ctx: _Ctx
) -> None:
    team, _user, project, _solution, run = _ctx
    item = EvalItem(item_id="i-2", suite="s", query={"q": "?"}, ground_truth={}, metadata={})
    exec_result = ExecutionResult(
        output={},
        output_kind="json",
        trace=ExecutionStep(uuid="root", name="root", level="workflow", status="FAILED"),
        error="boom",
    )

    persist_result(
        session,
        team_id=team.id,
        run_id=run.id,
        item=item,
        attempt_idx=0,
        exec_result=exec_result,
        verdicts=[],
        outcome=GraderOutcome.ERROR,
    )
    session.commit()

    persisted = ResultRepo(session).list_for_run(run.id)
    assert persisted[0].status == ResultStatus.ERROR
    assert persisted[0].error == "boom"


def test_persist_result_marks_timeout_status(session: Session, _ctx: _Ctx) -> None:
    team, _user, project, _solution, run = _ctx
    item = EvalItem(item_id="i-3", suite="s", query={"q": "?"}, ground_truth={}, metadata={})
    exec_result = ExecutionResult(
        output={},
        output_kind="json",
        trace=ExecutionStep(uuid="root", name="root", level="workflow", status="FAILED"),
        error="TIMEOUT",
    )

    persist_result(
        session,
        team_id=team.id,
        run_id=run.id,
        item=item,
        attempt_idx=0,
        exec_result=exec_result,
        verdicts=[],
        outcome=GraderOutcome.TIMEOUT,
    )
    session.commit()

    persisted = ResultRepo(session).list_for_run(run.id)
    assert persisted[0].status == ResultStatus.TIMEOUT


def test_persist_result_rejects_duplicate(session: Session, _ctx: _Ctx) -> None:
    team, _user, project, _solution, run = _ctx
    item = EvalItem(item_id="i-4", suite="s", query={}, ground_truth={}, metadata={})
    exec_result = ExecutionResult(
        output={},
        output_kind="json",
        trace=ExecutionStep(uuid="root", name="root", level="workflow", status="COMPLETED"),
    )

    persist_result(
        session,
        team_id=team.id,
        run_id=run.id,
        item=item,
        attempt_idx=0,
        exec_result=exec_result,
        verdicts=[],
        outcome=GraderOutcome.PASS,
    )
    session.commit()

    with pytest.raises(IntegrityError):
        persist_result(
            session,
            team_id=team.id,
            run_id=run.id,
            item=item,
            attempt_idx=0,
            exec_result=exec_result,
            verdicts=[],
            outcome=GraderOutcome.PASS,
        )
        session.commit()
