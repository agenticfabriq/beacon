"""GET /v1/projects/{project_id}/review-queue."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_registry.items import ItemService
from beacon_registry.provenance import ProvenanceService
from beacon_registry.types import ActorType, EvalItemTier
from beacon_storage.models.tenancy import User
from sqlalchemy import select

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_storage.models.tenancy import Project, Team
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _alice_user(session: Session) -> User:
    user = session.scalar(select(User).where(User.email == "alice@example.com"))
    assert user is not None
    return user


def _create_item(
    session: Session,
    *,
    team_id: UUID,
    user_id: UUID,
    tier: EvalItemTier = EvalItemTier.MODEL_PROPOSED,
    suite: str = "bird",
    item_input: dict[str, object] | None = None,
) -> UUID:
    return ItemService(session).create_item(
        tier=tier,
        suite=suite,
        team_id=team_id,
        solution_id=None,
        dataset_version="v",
        item_input=item_input or {"q": "?"},
        gold_answer=None,
        item_metadata={},
        created_by=user_id,
    )


def _promote(
    session: Session,
    *,
    item_id: UUID,
    new_tier: EvalItemTier,
    user_id: UUID,
    actor_type: ActorType = ActorType.SYSTEM,
) -> None:
    ProvenanceService(session).promote_tier(
        item_id=item_id,
        new_tier=new_tier,
        actor_type=actor_type,
        actor_id=str(user_id),
        created_by=user_id if actor_type == ActorType.HUMAN else None,
        reason="r",
        evidence={},
    )


def test_review_queue_lists_execution_confirmed(
    api_client: TestClient,
    alice_api_key: str,
    alice_team_membership: Team,
    alice_project: Project,
    session: Session,
) -> None:
    user = _alice_user(session)

    queued_id = _create_item(
        session,
        team_id=alice_team_membership.id,
        user_id=user.id,
        item_input={"q": "queued"},
    )
    _promote(
        session,
        item_id=queued_id,
        new_tier=EvalItemTier.EXECUTION_CONFIRMED,
        user_id=user.id,
    )

    _create_item(
        session,
        team_id=alice_team_membership.id,
        user_id=user.id,
        item_input={"q": "not yet"},
    )

    done_id = _create_item(
        session,
        team_id=alice_team_membership.id,
        user_id=user.id,
        item_input={"q": "done"},
    )
    _promote(
        session,
        item_id=done_id,
        new_tier=EvalItemTier.EXECUTION_CONFIRMED,
        user_id=user.id,
    )
    _promote(
        session,
        item_id=done_id,
        new_tier=EvalItemTier.HUMAN_VERIFIED,
        user_id=user.id,
        actor_type=ActorType.HUMAN,
    )
    session.commit()

    response = api_client.get(
        f"/v1/projects/{alice_project.id}/review-queue",
        headers={"X-API-Key": alice_api_key},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["item_id"] == str(queued_id)


def test_review_queue_requires_auth(
    api_client: TestClient,
    alice_project: Project,
) -> None:
    response = api_client.get(f"/v1/projects/{alice_project.id}/review-queue")

    assert response.status_code == 401


def test_review_queue_filters_by_suite(
    api_client: TestClient,
    alice_api_key: str,
    alice_team_membership: Team,
    alice_project: Project,
    session: Session,
) -> None:
    user = _alice_user(session)
    for suite in ("bird", "dabstep"):
        item_id = _create_item(
            session,
            team_id=alice_team_membership.id,
            user_id=user.id,
            suite=suite,
            item_input={"s": suite},
        )
        _promote(
            session,
            item_id=item_id,
            new_tier=EvalItemTier.EXECUTION_CONFIRMED,
            user_id=user.id,
        )
    session.commit()

    response = api_client.get(
        f"/v1/projects/{alice_project.id}/review-queue?suite=bird",
        headers={"X-API-Key": alice_api_key},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["suite"] == "bird"
