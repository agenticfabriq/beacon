"""EvalItem model: temporal versions keyed by item_id and valid_from."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_storage.models.eval_items import EvalItem, EvalItemTier
from beacon_storage.models.tenancy import Team, User
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@pytest.fixture
def _ctx(session: Session) -> tuple[Team, User]:
    team = Team(name="reg-team")
    user = User(email="reg@example.com", name="R")
    session.add_all([team, user])
    session.flush()
    return team, user


@pytest.mark.integration
def test_create_eval_item_minimal(session: Session, _ctx: tuple[Team, User]) -> None:
    team, _ = _ctx
    item_id = uuid4()
    item = EvalItem(
        item_id=item_id,
        valid_from=datetime.now(UTC),
        valid_to=None,
        tier=EvalItemTier.MODEL_PROPOSED,
        suite="bird_minidev_v2",
        team_id=team.id,
        solution_id=None,
        dataset_version="v2-2026-06-05",
        item_input={"question": "How many students?", "db": "california_schools"},
        gold_answer=None,
        item_metadata={"source": "production_trace"},
        question_hash="0" * 64,
        embedding=None,
        evidence=None,
    )
    session.add(item)
    session.commit()

    assert item.item_id == item_id
    assert item.tier == EvalItemTier.MODEL_PROPOSED
    assert item.valid_to is None


@pytest.mark.integration
def test_eval_item_shared_team_id_null(session: Session) -> None:
    item_id = uuid4()
    item = EvalItem(
        item_id=item_id,
        valid_from=datetime.now(UTC),
        valid_to=None,
        tier=EvalItemTier.HUMAN_VERIFIED,
        suite="bird_minidev_v2",
        team_id=None,
        solution_id=None,
        dataset_version="v2",
        item_input={"q": "?"},
        gold_answer={"sql": "SELECT 1"},
        item_metadata={},
        question_hash=None,
        embedding=None,
        evidence=None,
    )
    session.add(item)
    session.commit()

    assert item.team_id is None


@pytest.mark.integration
def test_two_versions_same_item_id_distinct_valid_from(
    session: Session, _ctx: tuple[Team, User]
) -> None:
    team, _ = _ctx
    item_id = uuid4()
    t0 = datetime(2026, 6, 1, tzinfo=UTC)
    t1 = datetime(2026, 6, 5, tzinfo=UTC)
    v0 = EvalItem(
        item_id=item_id,
        valid_from=t0,
        valid_to=t1,
        tier=EvalItemTier.MODEL_PROPOSED,
        suite="s",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "v0"},
        gold_answer=None,
        item_metadata={},
        question_hash=None,
        embedding=None,
        evidence=None,
    )
    v1 = EvalItem(
        item_id=item_id,
        valid_from=t1,
        valid_to=None,
        tier=EvalItemTier.EXECUTION_CONFIRMED,
        suite="s",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "v1"},
        gold_answer={"answer": "yes"},
        item_metadata={},
        question_hash=None,
        embedding=None,
        evidence=None,
    )
    session.add_all([v0, v1])
    session.commit()

    rows = list(
        session.scalars(
            select(EvalItem).where(EvalItem.item_id == item_id).order_by(EvalItem.valid_from)
        )
    )

    assert len(rows) == 2
    assert rows[0].valid_to == t1
    assert rows[1].valid_to is None


@pytest.mark.integration
def test_duplicate_valid_from_for_same_item_rejected(
    session: Session, _ctx: tuple[Team, User]
) -> None:
    team, _ = _ctx
    item_id = uuid4()
    ts = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
    v0 = EvalItem(
        item_id=item_id,
        valid_from=ts,
        valid_to=None,
        tier=EvalItemTier.MODEL_PROPOSED,
        suite="s",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "?"},
        gold_answer=None,
        item_metadata={},
        question_hash=None,
        embedding=None,
        evidence=None,
    )
    session.add(v0)
    session.commit()
    session.expunge(v0)

    duplicate = EvalItem(
        item_id=item_id,
        valid_from=ts,
        valid_to=None,
        tier=EvalItemTier.MODEL_PROPOSED,
        suite="s",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "dup"},
        gold_answer=None,
        item_metadata={},
        question_hash=None,
        embedding=None,
        evidence=None,
    )
    session.add(duplicate)

    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.integration
def test_second_active_version_for_same_item_rejected(
    session: Session, _ctx: tuple[Team, User]
) -> None:
    team, _ = _ctx
    item_id = uuid4()
    t0 = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 6, 5, 12, 0, 1, tzinfo=UTC)
    first = EvalItem(
        item_id=item_id,
        valid_from=t0,
        valid_to=None,
        tier=EvalItemTier.MODEL_PROPOSED,
        suite="s",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "first"},
        gold_answer=None,
        item_metadata={},
        question_hash=None,
        embedding=None,
        evidence=None,
    )
    second = EvalItem(
        item_id=item_id,
        valid_from=t1,
        valid_to=None,
        tier=EvalItemTier.EXECUTION_CONFIRMED,
        suite="s",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "second"},
        gold_answer=None,
        item_metadata={},
        question_hash=None,
        embedding=None,
        evidence=None,
    )
    session.add_all([first, second])

    with pytest.raises(IntegrityError):
        session.commit()
