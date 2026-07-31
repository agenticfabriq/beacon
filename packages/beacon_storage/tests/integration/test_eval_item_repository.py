"""EvalItemRepo active-row semantics and filtered listings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.tenancy import Team, User
from beacon_storage.repository.eval_items import EvalItemRepo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


pytestmark = pytest.mark.integration


@pytest.fixture
def _ctx(session: Session) -> tuple[Team, User]:
    team = Team(name="repo-reg")
    user = User(email="rr@example.com", name="R")
    session.add_all([team, user])
    session.flush()
    return team, user


def test_insert_new_version_then_get_active(session: Session, _ctx: tuple[Team, User]) -> None:
    team, user = _ctx
    item_id = uuid4()
    repo = EvalItemRepo(session)

    repo.insert_new_version(
        item_id=item_id,
        valid_from=datetime.now(UTC),
        tier=EvalItemTier.MODEL_PROPOSED,
        suite="bird",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "?"},
        gold_answer=None,
        item_metadata={},
        created_by=user.id,
    )

    got = repo.get_active(item_id)
    assert got is not None
    assert got.tier == EvalItemTier.MODEL_PROPOSED


def test_repo_create_uses_uuid7(session: Session, _ctx: tuple[Team, User]) -> None:
    team, user = _ctx
    repo = EvalItemRepo(session)

    item = repo.create(
        tier=EvalItemTier.MODEL_PROPOSED,
        suite="bird",
        team_id=team.id,
        item_input={"q": "?"},
        created_by=user.id,
    )

    assert item.item_id.version == 7


def test_supersede_marks_old_valid_to(session: Session, _ctx: tuple[Team, User]) -> None:
    team, user = _ctx
    item_id = uuid4()
    repo = EvalItemRepo(session)
    t0 = datetime.now(UTC)
    repo.insert_new_version(
        item_id=item_id,
        valid_from=t0,
        tier=EvalItemTier.MODEL_PROPOSED,
        suite="b",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "v0"},
        gold_answer=None,
        item_metadata={},
        created_by=user.id,
    )
    t1 = t0 + timedelta(seconds=1)
    repo.set_valid_to(item_id=item_id, valid_from=t0, valid_to=t1)
    repo.insert_new_version(
        item_id=item_id,
        valid_from=t1,
        tier=EvalItemTier.EXECUTION_CONFIRMED,
        suite="b",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "v1"},
        gold_answer={"answer": "yes"},
        item_metadata={},
        created_by=user.id,
    )

    active = repo.get_active(item_id)
    history = repo.list_history(item_id)

    assert active is not None
    assert active.tier == EvalItemTier.EXECUTION_CONFIRMED
    assert len(history) == 2
    assert history[0].valid_to == t1
    assert history[1].valid_to is None


def test_list_active_by_suite_and_tier(session: Session, _ctx: tuple[Team, User]) -> None:
    team, user = _ctx
    repo = EvalItemRepo(session)
    for i in range(3):
        repo.insert_new_version(
            item_id=uuid4(),
            valid_from=datetime.now(UTC) + timedelta(microseconds=i),
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite="bird",
            team_id=team.id,
            solution_id=None,
            dataset_version="v",
            item_input={"i": i},
            gold_answer={"x": i},
            item_metadata={},
            created_by=user.id,
        )
    for i in range(2):
        repo.insert_new_version(
            item_id=uuid4(),
            valid_from=datetime.now(UTC) + timedelta(microseconds=100 + i),
            tier=EvalItemTier.MODEL_PROPOSED,
            suite="bird",
            team_id=team.id,
            solution_id=None,
            dataset_version="v",
            item_input={"i": 100 + i},
            gold_answer=None,
            item_metadata={},
            created_by=user.id,
        )

    verified = repo.list_active(suite="bird", tier=EvalItemTier.HUMAN_VERIFIED)
    proposed = repo.list_active(suite="bird", tier=EvalItemTier.MODEL_PROPOSED)

    assert len(verified) == 3
    assert len(proposed) == 2


def test_list_active_filters_team_id_with_shared_passthrough(
    session: Session, _ctx: tuple[Team, User]
) -> None:
    team, user = _ctx
    repo = EvalItemRepo(session)
    repo.insert_new_version(
        item_id=uuid4(),
        valid_from=datetime.now(UTC),
        tier=EvalItemTier.HUMAN_VERIFIED,
        suite="x",
        team_id=None,
        solution_id=None,
        dataset_version="v",
        item_input={"shared": True},
        gold_answer={},
        item_metadata={},
        created_by=user.id,
    )
    repo.insert_new_version(
        item_id=uuid4(),
        valid_from=datetime.now(UTC) + timedelta(microseconds=1),
        tier=EvalItemTier.HUMAN_VERIFIED,
        suite="x",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"shared": False},
        gold_answer={},
        item_metadata={},
        created_by=user.id,
    )

    for_team = repo.list_active(
        suite="x",
        tier=EvalItemTier.HUMAN_VERIFIED,
        team_id=team.id,
    )
    shared_only = repo.list_active(
        suite="x",
        tier=EvalItemTier.HUMAN_VERIFIED,
        team_id=None,
    )

    assert len(for_team) == 2
    assert len(shared_only) == 1
