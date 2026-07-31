"""SuiteService create, add, replace, list, and conflict behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_registry.errors import DuplicateSuiteError, SuiteNotFoundError
from beacon_registry.items import ItemService
from beacon_registry.suites import SuiteService
from beacon_registry.types import EvalItemTier
from beacon_storage.models.tenancy import Project, Team, User

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


pytestmark = pytest.mark.integration


@pytest.fixture
def _ctx(session: Session) -> tuple[Team, User, Project]:
    team = Team(name="ss-team")
    user = User(email="ss@example.com", name="S")
    session.add_all([team, user])
    session.flush()
    project = Project(team_id=team.id, name="ss-proj", created_by=user.id)
    session.add(project)
    session.flush()
    return team, user, project


def _make_items(
    item_service: ItemService,
    *,
    team_id: UUID,
    user_id: UUID,
    count: int,
    start: int = 0,
) -> list[UUID]:
    item_ids: list[UUID] = []
    for index in range(start, start + count):
        item_ids.append(
            item_service.create_item(
                tier=EvalItemTier.HUMAN_VERIFIED,
                suite="b",
                team_id=team_id,
                solution_id=None,
                dataset_version="v",
                item_input={"i": index},
                gold_answer={},
                item_metadata={},
                created_by=user_id,
            )
        )
    return item_ids


def test_create_suite(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    team, user, project = _ctx
    suite = SuiteService(session).create(
        project_id=project.id,
        team_id=team.id,
        name="curated-50",
        description="d",
        method="separability_gain",
        suite_metadata={"n": 50},
        created_by=user.id,
    )

    assert suite.name == "curated-50"


def test_duplicate_suite_name_raises(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    team, user, project = _ctx
    service = SuiteService(session)
    service.create(
        project_id=project.id,
        team_id=team.id,
        name="dupe",
        description="",
        method="manual",
        suite_metadata={},
        created_by=user.id,
    )
    session.commit()

    with pytest.raises(DuplicateSuiteError):
        service.create(
            project_id=project.id,
            team_id=team.id,
            name="dupe",
            description="",
            method="manual",
            suite_metadata={},
            created_by=user.id,
        )


def test_add_items_idempotent(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    team, user, project = _ctx
    suite_service = SuiteService(session)
    item_service = ItemService(session)
    suite = suite_service.create(
        project_id=project.id,
        team_id=team.id,
        name="x",
        description="",
        method="manual",
        suite_metadata={},
        created_by=user.id,
    )
    item_ids = _make_items(item_service, team_id=team.id, user_id=user.id, count=3)

    first_count = suite_service.add_items(suite_id=suite.id, item_ids=item_ids)
    second_count = suite_service.add_items(suite_id=suite.id, item_ids=item_ids)

    assert first_count == 3
    assert second_count == 0
    assert len(suite_service.list_item_ids(suite.id)) == 3


def test_list_suites_for_project(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    team, user, project = _ctx
    service = SuiteService(session)
    service.create(
        project_id=project.id,
        team_id=team.id,
        name="b",
        description="",
        method="manual",
        suite_metadata={},
        created_by=user.id,
    )
    service.create(
        project_id=project.id,
        team_id=team.id,
        name="a",
        description="",
        method="manual",
        suite_metadata={},
        created_by=user.id,
    )

    names = [suite.name for suite in service.list_for_project(project.id)]

    assert names == ["a", "b"]


def test_get_suite_not_found_raises(session: Session) -> None:
    with pytest.raises(SuiteNotFoundError):
        SuiteService(session).get_or_raise(uuid4())


def test_replace_items_overwrites(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    team, user, project = _ctx
    suite_service = SuiteService(session)
    item_service = ItemService(session)
    suite = suite_service.create(
        project_id=project.id,
        team_id=team.id,
        name="cur",
        description="",
        method="separability_gain",
        suite_metadata={},
        created_by=user.id,
    )
    old_ids = _make_items(item_service, team_id=team.id, user_id=user.id, count=5)
    new_ids = _make_items(
        item_service,
        team_id=team.id,
        user_id=user.id,
        count=3,
        start=100,
    )

    suite_service.add_items(suite_id=suite.id, item_ids=old_ids)
    suite_service.replace_items(suite_id=suite.id, item_ids=new_ids)
    listed = suite_service.list_item_ids(suite.id)

    assert sorted(str(item_id) for item_id in listed) == sorted(str(item_id) for item_id in new_ids)
