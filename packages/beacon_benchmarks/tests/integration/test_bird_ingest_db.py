from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_benchmarks.bird_minidev.ingest_items import BirdTask, ingest_bird_tasks
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_storage.models.eval_items import EvalItem, EvalItemTier
from beacon_storage.repository.provenance import ProvenanceRepo
from beacon_storage.repository.teams import TeamRepo
from sqlalchemy import select, text

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _seed_team_and_user(session: Session) -> tuple[UUID, UUID]:
    team = TeamRepo(session).create(name=f"t-{uuid4().hex[:8]}", description="bird ingest test")
    user = UserService(session).upsert_from_oidc(
        OidcClaims(subject=f"u-{uuid4().hex[:8]}", email=f"{uuid4().hex[:8]}@x.test", name="t"),
    )
    session.flush()
    return team.id, user.id


def _tasks() -> list[BirdTask]:
    return [
        BirdTask(
            question_id=11,
            db_id="california_schools",
            question="Q11",
            evidence="E11",
            sql="SELECT 11",
            difficulty="simple",
        ),
        BirdTask(
            question_id=12,
            db_id="card_games",
            question="Q12",
            evidence="",
            sql="SELECT 12",
            difficulty="moderate",
        ),
    ]


def _active_item(session: Session, *, team_id: UUID, question_hash: str) -> EvalItem | None:
    return session.scalar(
        select(EvalItem).where(
            EvalItem.question_hash == question_hash,
            EvalItem.team_id == team_id,
            EvalItem.valid_to.is_(None),
        )
    )


def test_ingest_bird_tasks_inserts_items_and_provenance(session: Session) -> None:
    team_id, user_id = _seed_team_and_user(session)

    result = ingest_bird_tasks(session, team_id=team_id, tasks=_tasks(), created_by=user_id)

    assert result.inserted == 2
    assert result.skipped == 0

    a = _active_item(session, team_id=team_id, question_hash="bird:v2:11")
    assert a is not None
    assert a.suite == "bird_minidev_v2"
    assert a.dataset_version == "v2-2025-07-22"
    assert a.tier == EvalItemTier.HUMAN_VERIFIED
    assert a.item_input == {
        "db_id": "california_schools",
        "question": "Q11",
        "evidence": "E11",
    }
    assert a.gold_answer == {"sql": "SELECT 11"}
    assert a.item_metadata["source"] == "bird-minidev-v2"
    assert a.item_metadata["question_id"] == 11

    prov = ProvenanceRepo(session).list_for_team(team_id)
    reasons = {event.reason for event in prov}
    assert "bird-minidev-v2 public-source ingest" in reasons


def test_ingest_bird_tasks_is_idempotent(session: Session) -> None:
    team_id, user_id = _seed_team_and_user(session)

    first = ingest_bird_tasks(session, team_id=team_id, tasks=_tasks(), created_by=user_id)
    second = ingest_bird_tasks(session, team_id=team_id, tasks=_tasks(), created_by=user_id)

    assert first.inserted == 2 and first.skipped == 0
    assert second.inserted == 0 and second.skipped == 2

    n_items = session.execute(
        text("SELECT count(*) FROM eval_items WHERE suite = 'bird_minidev_v2' AND team_id = :tid"),
        {"tid": team_id},
    ).scalar_one()
    assert n_items == 2
