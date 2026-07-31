from typing import Protocol
from uuid import UUID

import pytest
from beacon_registry.types import ActorType, EvalItemTier
from beacon_storage.models.eval_items import EvalItem
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.provenance import ProvenanceRepo
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _World(Protocol):
    acme_team_id: UUID
    chat_to_data_id: UUID
    alice_id: UUID
    alice_key: str
    bob_key: str
    carol_key: str


def _seed_item(
    session: Session,
    world: _World,
    *,
    tier: EvalItemTier = EvalItemTier.EXECUTION_CONFIRMED,
) -> EvalItem:
    return EvalItemRepo(session).create(
        tier=tier,
        suite="bird_minidev_v2",
        team_id=world.acme_team_id,
        item_input={"question": "Q-promote"},
        gold_answer={"sql": "SELECT 1"},
        item_metadata={"source": "task-12-test"},
        created_by=world.alice_id,
    )


def test_project_contributor_promotes_with_project_scope(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    item = _seed_item(session, world)
    session.commit()

    response = api_client.post(
        f"/v1/registry/items/{item.item_id}/promote",
        headers={"X-API-Key": world.bob_key},
        params={"project_id": str(world.chat_to_data_id)},
        json={"new_tier": "human_verified", "reason": "manual override"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["item_id"] == str(item.item_id)
    assert body["prior_tier"] == "execution_confirmed"
    assert body["new_tier"] == "human_verified"


def test_promote_records_provenance_event(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    item = _seed_item(session, world)
    session.commit()

    response = api_client.post(
        f"/v1/registry/items/{item.item_id}/promote",
        headers={"X-API-Key": world.bob_key},
        params={"project_id": str(world.chat_to_data_id)},
        json={"new_tier": "human_verified", "reason": "verified by reviewer"},
    )

    assert response.status_code == 200, response.text
    session.expire_all()
    events = ProvenanceRepo(session).list_for_item(item.item_id)
    assert any(
        event.actor_type == ActorType.HUMAN
        and event.prior_tier == EvalItemTier.EXECUTION_CONFIRMED
        and event.new_tier == EvalItemTier.HUMAN_VERIFIED
        for event in events
    )


def test_invalid_tier_transition_rejected(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    item = _seed_item(session, world, tier=EvalItemTier.MODEL_PROPOSED)
    session.commit()

    response = api_client.post(
        f"/v1/registry/items/{item.item_id}/promote",
        headers={"X-API-Key": world.bob_key},
        params={"project_id": str(world.chat_to_data_id)},
        json={"new_tier": "human_verified", "reason": "skip a tier"},
    )

    assert response.status_code == 400
    assert "transition" in response.json()["detail"].lower()


def test_promote_without_project_id_requires_admin(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    item = _seed_item(session, world)
    session.commit()

    response = api_client.post(
        f"/v1/registry/items/{item.item_id}/promote",
        headers={"X-API-Key": world.bob_key},
        json={"new_tier": "human_verified", "reason": "missing scope"},
    )

    assert response.status_code == 403


def test_outsider_cannot_promote_with_project_scope(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    item = _seed_item(session, world)
    session.commit()

    response = api_client.post(
        f"/v1/registry/items/{item.item_id}/promote",
        headers={"X-API-Key": world.carol_key},
        params={"project_id": str(world.chat_to_data_id)},
        json={"new_tier": "human_verified", "reason": "outside project"},
    )

    assert response.status_code == 403
