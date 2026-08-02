from typing import Protocol
from uuid import UUID

import pytest
from beacon_storage.models.eval_items import EvalItem, EvalItemTier
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_ui.dashboard.panels.review_queue import (
    review_item_gold,
    review_item_question,
)
from dashboard_panel_test import run_dashboard_panel  # type: ignore[import-not-found]
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _World(Protocol):
    alice_key: str
    acme_team_id: UUID
    chat_to_data_id: UUID
    alice_id: UUID


def _seed_pending(session: Session, world: _World) -> EvalItem:
    item = EvalItemRepo(session).create(
        tier=EvalItemTier.EXECUTION_CONFIRMED,
        suite="task22_review_suite",
        team_id=world.acme_team_id,
        item_input={"question": "Which city has the highest revenue?"},
        gold_answer={"sql": "SELECT city FROM revenue ORDER BY amount DESC LIMIT 1"},
        item_metadata={
            "candidates": [
                {
                    "solution_name": "dummy-runner",
                    "pass_status": "pass",
                    "output": {"sql": "SELECT city FROM revenue ORDER BY amount DESC LIMIT 1"},
                }
            ],
        },
        created_by=world.alice_id,
    )
    session.commit()
    return item


def test_review_item_helpers_handle_current_and_enriched_shapes() -> None:
    item = {
        "item_id": "12345678-0000-0000-0000-000000000000",
        "item_input": {"question": "What changed?"},
        "gold_answer": {"answer": "revenue"},
        "metadata": {},
    }

    assert review_item_question(item) == "What changed?"
    assert review_item_gold(item) == {"answer": "revenue"}


def test_review_queue_panel_renders_empty(
    api_client: TestClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = run_dashboard_panel("review_queue", api_client, world, monkeypatch)
    assert not at.exception, at.exception


def test_review_queue_panel_renders_pending_items(
    api_client: TestClient,
    world: _World,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_pending(session, world)

    at = run_dashboard_panel("review_queue", api_client, world, monkeypatch)

    assert not at.exception, at.exception
    assert any(getattr(radio, "label", "") == "Decision" for radio in at.get("radio"))
