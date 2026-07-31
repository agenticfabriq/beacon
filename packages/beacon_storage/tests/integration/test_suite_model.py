"""Suite, EvalItemSuite, and ProductionTrace model wiring."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_storage.models.eval_items import EvalItem, EvalItemTier
from beacon_storage.models.production_traces import ProductionTrace
from beacon_storage.models.suites import EvalItemSuite, Suite
from beacon_storage.models.tenancy import Project, Team, User
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@pytest.fixture
def _ctx(session: Session) -> tuple[Team, User, Project]:
    team = Team(name="suite-team")
    user = User(email="suite@example.com", name="S")
    session.add_all([team, user])
    session.flush()
    project = Project(team_id=team.id, name="suite-proj", created_by=user.id)
    session.add(project)
    session.flush()
    return team, user, project


@pytest.mark.integration
def test_create_suite(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    team, user, project = _ctx
    suite = Suite(
        project_id=project.id,
        team_id=team.id,
        name="curated-50-2026-06-05",
        description="separability-curated 50-item gate set",
        method="separability_gain",
        created_by=user.id,
    )

    session.add(suite)
    session.commit()

    assert suite.id is not None
    assert suite.method == "separability_gain"


@pytest.mark.integration
def test_suite_unique_name_per_project(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    team, user, project = _ctx
    first = Suite(
        project_id=project.id,
        team_id=team.id,
        name="dup",
        method="manual",
        created_by=user.id,
    )
    session.add(first)
    session.commit()
    second = Suite(
        project_id=project.id,
        team_id=team.id,
        name="dup",
        method="manual",
        created_by=user.id,
    )
    session.add(second)

    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.integration
def test_eval_item_suite_join(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    team, user, project = _ctx
    suite = Suite(
        project_id=project.id,
        team_id=team.id,
        name="joined",
        method="manual",
        created_by=user.id,
    )
    session.add(suite)
    session.flush()
    item_id = uuid4()
    item = EvalItem(
        item_id=item_id,
        valid_from=datetime.now(UTC),
        valid_to=None,
        tier=EvalItemTier.HUMAN_VERIFIED,
        suite="bird_minidev_v2",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "?"},
        gold_answer=None,
        item_metadata={},
    )
    session.add(item)
    session.flush()
    join = EvalItemSuite(suite_id=suite.id, item_id=item_id)
    session.add(join)
    session.commit()

    assert join.suite_id == suite.id


@pytest.mark.integration
def test_eval_item_suite_dupe_rejected(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    _team, user, project = _ctx
    suite = Suite(
        project_id=project.id,
        team_id=project.team_id,
        name="dup-join",
        method="manual",
        created_by=user.id,
    )
    session.add(suite)
    session.flush()
    item_id = uuid4()
    first = EvalItemSuite(suite_id=suite.id, item_id=item_id)
    session.add(first)
    session.commit()
    session.expunge(first)

    second = EvalItemSuite(suite_id=suite.id, item_id=item_id)
    session.add(second)
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.integration
def test_production_trace_raw_payload(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    team, _user, project = _ctx
    production_trace = ProductionTrace(
        team_id=team.id,
        project_id=project.id,
        solution_id="acme-chat-to-data",
        api_key_id=None,
        item_input={"question": "How many?", "db": "x"},
        item_output={"sql": "SELECT count(*) FROM x", "answer": "42"},
        trace_payload={"name": "root", "level": "workflow", "children": []},
        trace_metadata={"latency_ms": 234, "cost_usd": 0.012},
        is_eval_candidate=True,
        derived_trace_id=None,
    )
    session.add(production_trace)
    session.commit()

    assert production_trace.id is not None
    assert production_trace.is_eval_candidate is True
    assert production_trace.trace_metadata["latency_ms"] == 234
