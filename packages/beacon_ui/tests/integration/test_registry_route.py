"""GET /v1/registry/items + POST /v1/registry/items/{id}/promote."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from beacon_registry.items import ItemService
from beacon_registry.types import EvalItemTier
from beacon_storage.models.tenancy import User
from sqlalchemy import select

if TYPE_CHECKING:
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
    team_id: UUID | None,
    user_id: UUID | None,
    *,
    tier: EvalItemTier = EvalItemTier.MODEL_PROPOSED,
    suite: str = "bird",
) -> UUID:
    return ItemService(session).create_item(
        tier=tier,
        suite=suite,
        team_id=team_id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "?"},
        gold_answer=None,
        item_metadata={},
        created_by=user_id,
    )


def test_list_items_filters_by_suite_and_tier(
    api_client: TestClient,
    alice_api_key: str,
    alice_team_membership: Team,
    session: Session,
) -> None:
    user = _alice_user(session)
    _create_item(session, alice_team_membership.id, user.id, tier=EvalItemTier.MODEL_PROPOSED)
    _create_item(session, alice_team_membership.id, user.id, tier=EvalItemTier.HUMAN_VERIFIED)
    session.commit()

    response = api_client.get(
        "/v1/registry/items?suite=bird&tier=human_verified",
        headers={"X-API-Key": alice_api_key},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["tier"] == "human_verified"


def test_list_items_requires_auth(api_client: TestClient) -> None:
    response = api_client.get("/v1/registry/items?suite=bird")

    assert response.status_code == 401


def test_promote_advances_tier(
    api_client: TestClient,
    alice_api_key: str,
    alice_team_membership: Team,
    alice_project: Project,
    session: Session,
) -> None:
    user = _alice_user(session)
    item_id = _create_item(
        session,
        alice_team_membership.id,
        user.id,
        tier=EvalItemTier.MODEL_PROPOSED,
    )
    session.commit()

    response = api_client.post(
        f"/v1/registry/items/{item_id}/promote",
        params={"project_id": str(alice_project.id)},
        json={"new_tier": "execution_confirmed", "reason": "3 SUTs converged"},
        headers={"X-API-Key": alice_api_key},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["prior_tier"] == "model_proposed"
    assert body["new_tier"] == "execution_confirmed"


def test_promote_rejects_invalid_transition(
    api_client: TestClient,
    alice_api_key: str,
    alice_team_membership: Team,
    alice_project: Project,
    session: Session,
) -> None:
    user = _alice_user(session)
    item_id = _create_item(
        session,
        alice_team_membership.id,
        user.id,
        tier=EvalItemTier.MODEL_PROPOSED,
    )
    session.commit()

    response = api_client.post(
        f"/v1/registry/items/{item_id}/promote",
        params={"project_id": str(alice_project.id)},
        json={"new_tier": "human_verified", "reason": "skip"},
        headers={"X-API-Key": alice_api_key},
    )

    assert response.status_code == 400
    assert "transition" in response.json()["detail"].lower()


def test_promote_404_on_missing_item(
    api_client: TestClient,
    alice_api_key: str,
    alice_team_membership: Team,
    alice_project: Project,
) -> None:
    fake = uuid4()

    response = api_client.post(
        f"/v1/registry/items/{fake}/promote",
        params={"project_id": str(alice_project.id)},
        json={"new_tier": "execution_confirmed", "reason": "r"},
        headers={"X-API-Key": alice_api_key},
    )

    assert response.status_code == 404
    assert str(fake) in response.json()["detail"]
    assert alice_team_membership.id
