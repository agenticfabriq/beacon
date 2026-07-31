"""ProductionTraceRepo raw ingest operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_storage.models.tenancy import Project, Team, User
from beacon_storage.repository.production_traces import ProductionTraceRepo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


pytestmark = pytest.mark.integration


@pytest.fixture
def _ctx(session: Session) -> tuple[Team, User, Project]:
    team = Team(name="production-trace-repo")
    user = User(email="ptr@example.com", name="P")
    session.add_all([team, user])
    session.flush()
    project = Project(team_id=team.id, name="ptrp", created_by=user.id)
    session.add(project)
    session.flush()
    return team, user, project


def test_create_and_get(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    team, _user, project = _ctx
    repo = ProductionTraceRepo(session)

    trace = repo.create(
        team_id=team.id,
        project_id=project.id,
        solution_id="acme-chat-to-data",
        api_key_id=None,
        item_input={"question": "How many?"},
        item_output={"answer": "42"},
        trace_payload={"name": "root", "children": []},
        trace_metadata={"latency_ms": 234},
        is_eval_candidate=True,
        derived_trace_id=None,
    )
    got = repo.get(trace.id)

    assert got is not None
    assert got.id == trace.id
    assert got.trace_metadata["latency_ms"] == 234


def test_list_unprocessed_and_mark_processed(
    session: Session, _ctx: tuple[Team, User, Project]
) -> None:
    team, _user, project = _ctx
    repo = ProductionTraceRepo(session)
    first = repo.create(
        team_id=team.id,
        project_id=project.id,
        solution_id="acme-chat-to-data",
        api_key_id=None,
        item_input={"i": 1},
        item_output={"o": 1},
        trace_payload={"name": "first"},
        trace_metadata={},
        is_eval_candidate=False,
        derived_trace_id=None,
    )
    second = repo.create(
        team_id=team.id,
        project_id=project.id,
        solution_id="acme-chat-to-data",
        api_key_id=None,
        item_input={"i": 2},
        item_output={"o": 2},
        trace_payload={"name": "second"},
        trace_metadata={},
        is_eval_candidate=True,
        derived_trace_id=None,
    )

    repo.mark_processed(first.id)
    unprocessed = repo.list_unprocessed()

    assert [trace.id for trace in unprocessed] == [second.id]
    assert first.processed_at is not None
