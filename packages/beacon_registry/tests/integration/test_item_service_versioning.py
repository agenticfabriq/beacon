"""ItemService append-only versioning semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_registry.errors import EvalItemNotFoundError
from beacon_registry.items import ItemService
from beacon_registry.types import EvalItemTier
from beacon_storage.models.tenancy import Team, User

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


pytestmark = pytest.mark.integration


@pytest.fixture
def _ctx(session: Session) -> tuple[Team, User]:
    team = Team(name="item-svc")
    user = User(email="is@example.com", name="I")
    session.add_all([team, user])
    session.flush()
    return team, user


def test_create_item_writes_active_row(session: Session, _ctx: tuple[Team, User]) -> None:
    team, user = _ctx
    service = ItemService(session)
    item_id = service.create_item(
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

    assert item_id.version == 7
    active = service.get_active(item_id)

    assert active is not None
    assert active.tier == EvalItemTier.MODEL_PROPOSED
    assert active.valid_to is None


def test_update_item_supersedes_old_version(session: Session, _ctx: tuple[Team, User]) -> None:
    team, user = _ctx
    service = ItemService(session)
    item_id = service.create_item(
        tier=EvalItemTier.MODEL_PROPOSED,
        suite="bird",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "v0"},
        gold_answer=None,
        item_metadata={},
        created_by=user.id,
    )
    new_version = service.update_item(
        item_id=item_id,
        tier=EvalItemTier.EXECUTION_CONFIRMED,
        suite="bird",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "v1"},
        gold_answer={"a": "yes"},
        item_metadata={},
        created_by=user.id,
    )

    active = service.get_active(item_id)
    history = service.list_history(item_id)

    assert active is not None
    assert active.valid_from == new_version.valid_from
    assert active.tier == EvalItemTier.EXECUTION_CONFIRMED
    assert active.item_input == {"q": "v1"}
    assert len(history) == 2
    assert history[0].valid_to == new_version.valid_from
    assert history[0].item_input == {"q": "v0"}


def test_update_does_not_change_old_payload(session: Session, _ctx: tuple[Team, User]) -> None:
    team, user = _ctx
    service = ItemService(session)
    item_id = service.create_item(
        tier=EvalItemTier.MODEL_PROPOSED,
        suite="bird",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "original", "important": True},
        gold_answer={"sql": "SELECT 1"},
        item_metadata={"tag": "x"},
        created_by=user.id,
    )
    old_version = service.get_active(item_id)
    assert old_version is not None
    old_valid_from = old_version.valid_from

    service.update_item(
        item_id=item_id,
        tier=EvalItemTier.HUMAN_VERIFIED,
        suite="bird",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "corrected"},
        gold_answer={"sql": "SELECT 2"},
        item_metadata={"tag": "y"},
        created_by=user.id,
    )

    historic = service.get_version(item_id, old_valid_from)

    assert historic is not None
    assert historic.item_input == {"q": "original", "important": True}
    assert historic.gold_answer == {"sql": "SELECT 1"}
    assert historic.item_metadata == {"tag": "x"}
    assert historic.tier == EvalItemTier.MODEL_PROPOSED


def test_three_sequential_updates_produce_four_row_chain(
    session: Session, _ctx: tuple[Team, User]
) -> None:
    team, user = _ctx
    service = ItemService(session)
    item_id = service.create_item(
        tier=EvalItemTier.MODEL_PROPOSED,
        suite="b",
        team_id=team.id,
        solution_id=None,
        dataset_version="v",
        item_input={"v": 0},
        gold_answer=None,
        item_metadata={},
        created_by=user.id,
    )
    for i in range(1, 4):
        service.update_item(
            item_id=item_id,
            tier=EvalItemTier.MODEL_PROPOSED,
            suite="b",
            team_id=team.id,
            solution_id=None,
            dataset_version="v",
            item_input={"v": i},
            gold_answer=None,
            item_metadata={},
            created_by=user.id,
        )

    history = service.list_history(item_id)

    assert len(history) == 4
    for previous, next_version in zip(history[:-1], history[1:], strict=True):
        assert previous.valid_to == next_version.valid_from
    assert history[-1].valid_to is None


def test_update_nonexistent_item_raises(session: Session, _ctx: tuple[Team, User]) -> None:
    service = ItemService(session)

    with pytest.raises(EvalItemNotFoundError):
        service.update_item(
            item_id=uuid4(),
            tier=EvalItemTier.MODEL_PROPOSED,
            suite="b",
            team_id=None,
            solution_id=None,
            dataset_version="v",
            item_input={},
            gold_answer=None,
            item_metadata={},
            created_by=None,
        )


def test_get_active_returns_none_for_unknown_item(
    session: Session, _ctx: tuple[Team, User]
) -> None:
    service = ItemService(session)

    assert service.get_active(uuid4()) is None


def test_query_filters_by_suite_and_tier(session: Session, _ctx: tuple[Team, User]) -> None:
    team, user = _ctx
    service = ItemService(session)
    for i in range(3):
        service.create_item(
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite="x",
            team_id=team.id,
            solution_id=None,
            dataset_version="v",
            item_input={"i": i},
            gold_answer={},
            item_metadata={},
            created_by=user.id,
        )
    for i in range(2):
        service.create_item(
            tier=EvalItemTier.MODEL_PROPOSED,
            suite="x",
            team_id=team.id,
            solution_id=None,
            dataset_version="v",
            item_input={"i": 100 + i},
            gold_answer=None,
            item_metadata={},
            created_by=user.id,
        )

    human_verified = service.query(
        suite="x",
        tier=EvalItemTier.HUMAN_VERIFIED,
        team_id=team.id,
    )
    model_proposed = service.query(
        suite="x",
        tier=EvalItemTier.MODEL_PROPOSED,
        team_id=team.id,
    )

    assert len(human_verified) == 3
    assert len(model_proposed) == 2
