"""ProvenanceEvent model: append-only ledger of tier transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.provenance import ActorType, ProvenanceEvent
from beacon_storage.models.tenancy import Team, User
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@pytest.fixture
def _ctx(session: Session) -> tuple[Team, User]:
    team = Team(name="prov-team")
    user = User(email="prov@example.com", name="P")
    session.add_all([team, user])
    session.flush()
    return team, user


@pytest.mark.integration
def test_create_initial_event_prior_tier_null(session: Session, _ctx: tuple[Team, User]) -> None:
    team, _ = _ctx
    event = ProvenanceEvent(
        item_id=uuid4(),
        team_id=team.id,
        prior_tier=None,
        new_tier=EvalItemTier.MODEL_PROPOSED,
        actor_type=ActorType.SYSTEM,
        actor_id="trace_ingest_worker",
        created_by=None,
        reason="ingested from production trace",
        evidence={"trace_id": str(uuid4())},
    )
    session.add(event)
    session.commit()

    assert event.event_id is not None
    assert event.prior_tier is None
    assert event.new_tier == EvalItemTier.MODEL_PROPOSED


@pytest.mark.integration
def test_create_human_promotion_event(session: Session, _ctx: tuple[Team, User]) -> None:
    team, user = _ctx
    event = ProvenanceEvent(
        item_id=uuid4(),
        team_id=team.id,
        prior_tier=EvalItemTier.EXECUTION_CONFIRMED,
        new_tier=EvalItemTier.HUMAN_VERIFIED,
        actor_type=ActorType.HUMAN,
        actor_id=str(user.id),
        created_by=user.id,
        reason="reviewed in Review Queue; gold matches",
        evidence={"reviewer_notes": "checked SQL by hand"},
    )
    session.add(event)
    session.commit()

    assert event.created_by == user.id


@pytest.mark.integration
def test_check_constraint_rejects_invalid_actor_type(
    session: Session, _ctx: tuple[Team, User]
) -> None:
    team, _ = _ctx
    event = ProvenanceEvent(
        item_id=uuid4(),
        team_id=team.id,
        prior_tier=None,
        new_tier=EvalItemTier.MODEL_PROPOSED,
        actor_type=cast("ActorType", "alien"),
        actor_id="x",
        created_by=None,
        reason="r",
        evidence={},
    )
    session.add(event)

    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.integration
def test_check_constraint_rejects_invalid_new_tier(
    session: Session, _ctx: tuple[Team, User]
) -> None:
    team, _ = _ctx
    event = ProvenanceEvent(
        item_id=uuid4(),
        team_id=team.id,
        prior_tier=None,
        new_tier=cast("EvalItemTier", "archived"),
        actor_type=ActorType.SYSTEM,
        actor_id="x",
        created_by=None,
        reason="r",
        evidence={},
    )
    session.add(event)

    with pytest.raises(IntegrityError):
        session.commit()
