from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import pytest
from beacon_storage.models.eval_items import EvalItem, EvalItemTier
from beacon_storage.repository.eval_items import EvalItemRepo
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _World(Protocol):
    acme_team_id: UUID
    chat_to_data_id: UUID
    alice_id: UUID
    alice_key: str
    carol_key: str


def _seed_pending(
    session: Session,
    world: _World,
    *,
    count: int = 3,
    suite: str = "task11_review_suite",
) -> list[EvalItem]:
    repo = EvalItemRepo(session)
    return [
        repo.create(
            tier=EvalItemTier.EXECUTION_CONFIRMED,
            suite=suite,
            team_id=world.acme_team_id,
            item_input={"question": f"review item {idx}"},
            gold_answer={"sql": f"SELECT {idx}"},
            item_metadata={"source": "task-11-test"},
            created_by=world.alice_id,
        )
        for idx in range(count)
    ]


def test_queue_paginates(api_client: TestClient, world: _World, session: Session) -> None:
    _seed_pending(session, world)
    session.commit()

    response = api_client.get(
        f"/v1/projects/{world.chat_to_data_id}/review-queue",
        headers={"X-API-Key": world.alice_key},
        params={"suite": "task11_review_suite", "limit": 2, "offset": 0},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert len(body["items"]) == 2


def test_accept_promotes_to_human_verified(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    item = _seed_pending(session, world, count=1)[0]
    session.commit()

    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/review-queue/{item.item_id}/decide",
        headers={"X-API-Key": world.alice_key},
        json={"action": "accept", "reason": "matches gold"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["item_id"] == str(item.item_id)
    assert body["action"] == "accept"
    assert body["new_tier"] == "human_verified"
    assert body["actor"] == "alice@example.com"
    assert body["event_id"]


def test_reject_keeps_tier_but_marks_rejected(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    item = _seed_pending(session, world, count=1)[0]
    session.commit()

    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/review-queue/{item.item_id}/decide",
        headers={"X-API-Key": world.alice_key},
        json={"action": "reject", "reason": "ambiguous gold"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"] == "reject"
    assert body["new_tier"] == "execution_confirmed"
    assert body["rejected_at"] is not None


def test_defer_keeps_tier_and_returns_deferred_until(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    item = _seed_pending(session, world, count=1)[0]
    session.commit()
    before = datetime.now(UTC)

    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/review-queue/{item.item_id}/decide",
        headers={"X-API-Key": world.alice_key},
        json={"action": "defer", "reason": "need DBA input", "defer_days": 3},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"] == "defer"
    assert body["new_tier"] == "execution_confirmed"
    assert body["deferred_at"] is not None
    assert body["deferred_until"] is not None
    deferred_until = datetime.fromisoformat(body["deferred_until"])
    assert deferred_until >= before + timedelta(days=2)


def test_outsider_cannot_decide(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    item = _seed_pending(session, world, count=1)[0]
    session.commit()

    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/review-queue/{item.item_id}/decide",
        headers={"X-API-Key": world.carol_key},
        json={"action": "accept", "reason": "test"},
    )

    assert response.status_code == 403


def test_missing_reason_rejected(
    api_client: TestClient,
    world: _World,
    session: Session,
) -> None:
    item = _seed_pending(session, world, count=1)[0]
    session.commit()

    response = api_client.post(
        f"/v1/projects/{world.chat_to_data_id}/review-queue/{item.item_id}/decide",
        headers={"X-API-Key": world.alice_key},
        json={"action": "reject"},
    )

    assert response.status_code == 422
