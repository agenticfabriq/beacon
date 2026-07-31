"""TraceIngestService: SDK payload to production_traces rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_registry.ingestion import TraceIngestService
from beacon_registry.types import TraceIngestRequest
from beacon_storage.models.tenancy import Project, Team, User
from beacon_storage.repository.production_traces import ProductionTraceRepo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


pytestmark = pytest.mark.integration


@pytest.fixture
def _ctx(session: Session) -> tuple[Team, User, Project]:
    team = Team(name="ing-team")
    user = User(email="ing@example.com", name="I")
    session.add_all([team, user])
    session.flush()
    project = Project(team_id=team.id, name="ing-proj", created_by=user.id)
    session.add(project)
    session.flush()
    return team, user, project


def test_ingest_writes_production_trace(
    session: Session,
    _ctx: tuple[Team, User, Project],
) -> None:
    team, _user, project = _ctx
    service = TraceIngestService(session)
    request = TraceIngestRequest(
        solution_id="acme-chat-to-data",
        project_id=project.id,
        item_input={"question": "?", "db": "x"},
        item_output={"sql": "SELECT 1", "answer": "1"},
        trace={"name": "root", "level": "workflow", "children": []},
        metadata={"latency_ms": 234, "cost_usd": 0.012},
        is_eval_candidate=False,
    )

    result = service.ingest(request=request, team_id=team.id, api_key_id=None)
    session.commit()

    assert result.production_trace_id is not None
    assert result.derived_trace_id is None
    production_trace = ProductionTraceRepo(session).get(result.production_trace_id)
    assert production_trace is not None
    assert production_trace.solution_id == "acme-chat-to-data"
    assert production_trace.team_id == team.id
    assert production_trace.project_id == project.id
    assert production_trace.trace_payload == {
        "name": "root",
        "level": "workflow",
        "children": [],
    }
    assert production_trace.processed_at is None


def test_ingest_records_is_eval_candidate(
    session: Session,
    _ctx: tuple[Team, User, Project],
) -> None:
    team, _user, project = _ctx
    service = TraceIngestService(session)
    request = TraceIngestRequest(
        solution_id="x",
        project_id=project.id,
        item_input={"q": "?"},
        item_output={"a": "!"},
        trace={"name": "r", "level": "w", "children": []},
        metadata={},
        is_eval_candidate=True,
    )

    result = service.ingest(request=request, team_id=team.id, api_key_id=None)
    session.commit()

    production_trace = ProductionTraceRepo(session).get(result.production_trace_id)
    assert production_trace is not None
    assert production_trace.is_eval_candidate is True
    assert production_trace.derived_trace_id is None


def test_ingest_with_null_project_id(
    session: Session,
    _ctx: tuple[Team, User, Project],
) -> None:
    team, _user, _project = _ctx
    service = TraceIngestService(session)
    request = TraceIngestRequest(
        solution_id="x",
        project_id=None,
        item_input={"q": "?"},
        item_output={"a": "!"},
        trace={"name": "r", "level": "w", "children": []},
        metadata={},
        is_eval_candidate=False,
    )

    result = service.ingest(request=request, team_id=team.id, api_key_id=None)
    session.commit()

    production_trace = ProductionTraceRepo(session).get(result.production_trace_id)
    assert production_trace is not None
    assert production_trace.project_id is None
