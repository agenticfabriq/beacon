"""Repositories for Solution, Run, Result, Verdict, and Trace."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_storage.models.runs import HarnessMode, ResultStatus, RunStatus, VerdictOutcome
from beacon_storage.models.tenancy import Project, Team, User
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.traces import TraceRepo
from beacon_storage.repository.verdicts import VerdictRepo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def _ctx(session: Session) -> tuple[Team, User, Project]:
    t = Team(name="repo-team")
    u = User(email="repo@example.com", name="R")
    session.add_all([t, u])
    session.flush()
    p = Project(team_id=t.id, name="repo-proj", created_by=u.id)
    session.add(p)
    session.flush()
    return t, u, p


def test_solution_repo_create_and_get(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    t, u, _ = _ctx
    s = SolutionRepo(session).create(
        team_id=t.id,
        solution_id="dummy",
        version="0.2.0",
        owner_team=t.id,
        summary="x",
        supported_modes=["EVAL"],
        layers=[],
        created_by=u.id,
    )
    got = SolutionRepo(session).get_by_team_and_solution(t.id, "dummy", "0.2.0")
    assert got is not None
    assert got.id == s.id


def test_run_repo_lifecycle(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    t, u, p = _ctx
    s = SolutionRepo(session).create(
        team_id=t.id,
        solution_id="dummy",
        version="0.2.0",
        owner_team=t.id,
        summary="x",
        supported_modes=["EVAL"],
        layers=[],
        created_by=u.id,
    )
    r = RunRepo(session).create(
        team_id=t.id,
        project_id=p.id,
        solution_id=s.id,
        suite="s",
        dataset_version="v0",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        config={},
        created_by=u.id,
    )
    assert r.status == RunStatus.PENDING
    RunRepo(session).mark_running(r.id)
    running = RunRepo(session).get(r.id)
    assert running is not None
    assert running.status == RunStatus.RUNNING
    RunRepo(session).mark_completed(r.id)
    completed = RunRepo(session).get(r.id)
    assert completed is not None
    assert completed.status == RunStatus.COMPLETED
    assert completed.completed_at is not None


def test_result_verdict_trace_repos(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    t, u, p = _ctx
    s = SolutionRepo(session).create(
        team_id=t.id,
        solution_id="dummy",
        version="0.2.0",
        owner_team=t.id,
        summary="",
        supported_modes=["EVAL"],
        layers=[],
        created_by=u.id,
    )
    r = RunRepo(session).create(
        team_id=t.id,
        project_id=p.id,
        solution_id=s.id,
        suite="s",
        dataset_version="v0",
        mode=HarnessMode.EVAL,
        pass_idx=0,
        config={},
        created_by=u.id,
    )
    res = ResultRepo(session).create(
        team_id=t.id,
        project_id=p.id,
        run_id=r.id,
        item_id="i-1",
        attempt_idx=0,
        output={"answer": "42"},
        output_kind="answer",
        tokens_input=1,
        tokens_output=2,
        runtime_ms=3,
        status=ResultStatus.COMPLETED,
        outcome=VerdictOutcome.PASS,
        error=None,
    )
    v = VerdictRepo(session).create(
        team_id=t.id,
        project_id=p.id,
        result_id=res.id,
        grader="g",
        grader_version="v1",
        criterion="c",
        bool_value=True,
        value=1.0,
        justification="ok",
        raw_output={"x": 1},
    )
    tr = TraceRepo(session).create(
        team_id=t.id,
        project_id=p.id,
        result_id=res.id,
        step_tree={"name": "root"},
        object_storage_uri=None,
    )
    assert v.result_id == res.id
    assert tr.result_id == res.id
    listed = ResultRepo(session).list_for_run(r.id)
    assert len(listed) == 1
    vs = VerdictRepo(session).list_for_result(res.id)
    assert len(vs) == 1
