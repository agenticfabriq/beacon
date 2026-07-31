"""ReviewQueueService list, reject, and defer behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_registry.review_queue import ReviewQueueService
from beacon_registry.types import ActorType, EvalItemTier
from beacon_storage.models.tenancy import Project, Team, User
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.provenance import ProvenanceRepo

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_storage.models.eval_items import EvalItem
    from sqlalchemy.orm import Session


pytestmark = pytest.mark.integration


@pytest.fixture
def _ctx(session: Session) -> tuple[Team, User, Project]:
    team = Team(name="review-queue-team")
    user = User(email="rq@example.com", name="R")
    session.add_all([team, user])
    session.flush()
    project = Project(team_id=team.id, name="review-queue-project", created_by=user.id)
    session.add(project)
    session.flush()
    return team, user, project


def _seed_item(
    session: Session,
    *,
    team_id: UUID,
    tier: EvalItemTier = EvalItemTier.EXECUTION_CONFIRMED,
) -> EvalItem:
    return EvalItemRepo(session).create(
        tier=tier,
        suite="test_suite",
        team_id=team_id,
        solution_id=None,
        dataset_version="v1",
        item_input={"q": "?"},
        gold_answer=None,
        item_metadata={},
        created_by=None,
    )


def test_list_pending_returns_execution_confirmed_only(
    session: Session, _ctx: tuple[Team, User, Project]
) -> None:
    team, _, project = _ctx
    model_proposed = _seed_item(session, team_id=team.id, tier=EvalItemTier.MODEL_PROPOSED)
    execution_confirmed = _seed_item(session, team_id=team.id)
    session.commit()

    pending = ReviewQueueService(session).list_pending(project_id=project.id)

    pending_ids = {item.item_id for item in pending}
    assert execution_confirmed.item_id in pending_ids
    assert model_proposed.item_id not in pending_ids


def test_mark_rejected_excludes_from_list_pending_and_writes_event(
    session: Session, _ctx: tuple[Team, User, Project]
) -> None:
    team, user, project = _ctx
    item = _seed_item(session, team_id=team.id)
    session.commit()
    service = ReviewQueueService(session)

    service.mark_rejected(
        item_id=item.item_id,
        reason="duplicate of another item",
        actor_id=user.id,
    )
    session.commit()

    pending = service.list_pending(project_id=project.id)
    assert item.item_id not in {pending_item.item_id for pending_item in pending}

    events = ProvenanceRepo(session).list_for_item(item.item_id)
    assert len(events) == 1
    assert events[0].actor_type == ActorType.HUMAN
    assert events[0].prior_tier == EvalItemTier.EXECUTION_CONFIRMED
    assert events[0].new_tier == EvalItemTier.EXECUTION_CONFIRMED
    assert events[0].reason == "rejected: duplicate of another item"


def test_defer_pushes_to_back_of_queue_and_writes_event(
    session: Session, _ctx: tuple[Team, User, Project]
) -> None:
    team, user, project = _ctx
    early = _seed_item(session, team_id=team.id)
    late = _seed_item(session, team_id=team.id)
    session.commit()
    service = ReviewQueueService(session)

    service.defer(item_id=early.item_id, actor_id=user.id)
    session.commit()

    pending = service.list_pending(project_id=project.id)
    ordered_ids = [item.item_id for item in pending]
    assert ordered_ids.index(late.item_id) < ordered_ids.index(early.item_id)

    events = ProvenanceRepo(session).list_for_item(early.item_id)
    assert len(events) == 1
    assert events[0].actor_type == ActorType.HUMAN
    assert events[0].prior_tier == EvalItemTier.EXECUTION_CONFIRMED
    assert events[0].new_tier == EvalItemTier.EXECUTION_CONFIRMED
    assert events[0].reason == "deferred"
