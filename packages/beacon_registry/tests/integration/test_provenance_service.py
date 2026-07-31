"""ProvenanceService promotion writes item versions and event rows together."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from beacon_registry.errors import EvalItemNotFoundError, InvalidTierTransitionError
from beacon_registry.items import ItemService
from beacon_registry.provenance import ProvenanceService
from beacon_registry.types import ActorType, EvalItemTier
from beacon_storage.models.tenancy import Team, User
from beacon_storage.repository.provenance import ProvenanceRepo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


pytestmark = pytest.mark.integration


@pytest.fixture
def _ctx(session: Session) -> tuple[Team, User]:
    team = Team(name="prov-svc")
    user = User(email="ps@example.com", name="P")
    session.add_all([team, user])
    session.flush()
    return team, user


def _make_item(
    session: Session,
    *,
    team_id: UUID,
    user_id: UUID,
    tier: EvalItemTier = EvalItemTier.MODEL_PROPOSED,
) -> UUID:
    return ItemService(session).create_item(
        tier=tier,
        suite="b",
        team_id=team_id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "?"},
        gold_answer=None,
        item_metadata={},
        created_by=user_id,
    )


def test_promote_advances_tier_and_writes_event(session: Session, _ctx: tuple[Team, User]) -> None:
    team, user = _ctx
    item_id = _make_item(session, team_id=team.id, user_id=user.id)
    service = ProvenanceService(session)

    event = service.promote_tier(
        item_id=item_id,
        new_tier=EvalItemTier.EXECUTION_CONFIRMED,
        actor_type=ActorType.SYSTEM,
        actor_id="convergence_worker",
        created_by=None,
        reason="3 SUTs converged with conf>0.9",
        evidence={"converging_run_ids": []},
    )
    session.commit()

    active = ItemService(session).get_active(item_id)
    assert active is not None
    assert active.tier == EvalItemTier.EXECUTION_CONFIRMED

    events = ProvenanceRepo(session).list_for_item(item_id)
    assert len(events) == 1
    assert events[0].event_id == event.event_id
    assert events[0].prior_tier == EvalItemTier.MODEL_PROPOSED
    assert events[0].new_tier == EvalItemTier.EXECUTION_CONFIRMED
    assert events[0].actor_type == ActorType.SYSTEM


def test_full_promotion_chain_writes_two_events(session: Session, _ctx: tuple[Team, User]) -> None:
    team, user = _ctx
    item_id = _make_item(session, team_id=team.id, user_id=user.id)
    service = ProvenanceService(session)

    service.promote_tier(
        item_id=item_id,
        new_tier=EvalItemTier.EXECUTION_CONFIRMED,
        actor_type=ActorType.SYSTEM,
        actor_id="convergence",
        created_by=None,
        reason="converged",
        evidence={},
    )
    service.promote_tier(
        item_id=item_id,
        new_tier=EvalItemTier.HUMAN_VERIFIED,
        actor_type=ActorType.HUMAN,
        actor_id=str(user.id),
        created_by=user.id,
        reason="reviewed gold by hand",
        evidence={},
    )
    session.commit()

    events = ProvenanceRepo(session).list_for_item(item_id)
    assert len(events) == 2
    assert events[0].new_tier == EvalItemTier.EXECUTION_CONFIRMED
    assert events[1].new_tier == EvalItemTier.HUMAN_VERIFIED
    assert events[1].actor_type == ActorType.HUMAN
    assert events[1].created_by == user.id


def test_reverse_promotion_rejected_and_no_writes(
    session: Session, _ctx: tuple[Team, User]
) -> None:
    team, user = _ctx
    item_id = _make_item(
        session,
        team_id=team.id,
        user_id=user.id,
        tier=EvalItemTier.MODEL_PROPOSED,
    )
    session.commit()
    service = ProvenanceService(session)
    service.promote_tier(
        item_id=item_id,
        new_tier=EvalItemTier.EXECUTION_CONFIRMED,
        actor_type=ActorType.SYSTEM,
        actor_id="x",
        created_by=None,
        reason="r",
        evidence={},
    )
    service.promote_tier(
        item_id=item_id,
        new_tier=EvalItemTier.HUMAN_VERIFIED,
        actor_type=ActorType.SYSTEM,
        actor_id="x",
        created_by=None,
        reason="r",
        evidence={},
    )
    session.commit()

    events_before = ProvenanceRepo(session).list_for_item(item_id)
    assert len(events_before) == 2
    active_before = ItemService(session).get_active(item_id)
    assert active_before is not None

    with pytest.raises(InvalidTierTransitionError):
        service.promote_tier(
            item_id=item_id,
            new_tier=EvalItemTier.MODEL_PROPOSED,
            actor_type=ActorType.HUMAN,
            actor_id=str(user.id),
            created_by=user.id,
            reason="oops",
            evidence={},
        )
    session.rollback()

    events_after = ProvenanceRepo(session).list_for_item(item_id)
    assert len(events_after) == 2
    active_after = ItemService(session).get_active(item_id)
    assert active_after is not None
    assert active_after.valid_from == active_before.valid_from
    assert active_after.tier == EvalItemTier.HUMAN_VERIFIED


def test_skip_forward_rejected_and_no_writes(session: Session, _ctx: tuple[Team, User]) -> None:
    team, user = _ctx
    item_id = _make_item(
        session,
        team_id=team.id,
        user_id=user.id,
        tier=EvalItemTier.MODEL_PROPOSED,
    )
    session.commit()
    service = ProvenanceService(session)

    with pytest.raises(InvalidTierTransitionError):
        service.promote_tier(
            item_id=item_id,
            new_tier=EvalItemTier.HUMAN_VERIFIED,
            actor_type=ActorType.HUMAN,
            actor_id=str(user.id),
            created_by=user.id,
            reason="skip",
            evidence={},
        )
    session.rollback()

    assert ProvenanceRepo(session).list_for_item(item_id) == []
    assert len(ItemService(session).list_history(item_id)) == 1


def test_promote_unknown_item_raises_not_found(session: Session) -> None:
    service = ProvenanceService(session)

    with pytest.raises(EvalItemNotFoundError):
        service.promote_tier(
            item_id=uuid4(),
            new_tier=EvalItemTier.EXECUTION_CONFIRMED,
            actor_type=ActorType.SYSTEM,
            actor_id="x",
            created_by=None,
            reason="r",
            evidence={},
        )
