"""SuiteRepo and EvalItemSuite join operations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.tenancy import Project, Team, User
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.suites import SuiteRepo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


pytestmark = pytest.mark.integration


@pytest.fixture
def _ctx(session: Session) -> tuple[Team, User, Project]:
    team = Team(name="suite-repo")
    user = User(email="sr@example.com", name="S")
    session.add_all([team, user])
    session.flush()
    project = Project(team_id=team.id, name="srp", created_by=user.id)
    session.add(project)
    session.flush()
    return team, user, project


def test_create_and_get_by_name(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    team, user, project = _ctx
    repo = SuiteRepo(session)

    suite = repo.create(
        project_id=project.id,
        team_id=team.id,
        name="curated-50",
        description="d",
        method="separability_gain",
        suite_metadata={},
        created_by=user.id,
    )
    got = repo.get_by_project_and_name(project.id, "curated-50")

    assert got is not None
    assert got.id == suite.id


def test_add_items_and_list_items(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    team, user, project = _ctx
    suite_repo = SuiteRepo(session)
    item_repo = EvalItemRepo(session)
    suite = suite_repo.create(
        project_id=project.id,
        team_id=team.id,
        name="x",
        description="",
        method="manual",
        suite_metadata={},
        created_by=user.id,
    )
    item_ids: list[UUID] = []
    for i in range(3):
        item_id = uuid4()
        item_repo.insert_new_version(
            item_id=item_id,
            valid_from=datetime.now(UTC),
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite="bird",
            team_id=team.id,
            solution_id=None,
            dataset_version="v",
            item_input={"i": i},
            gold_answer={},
            item_metadata={},
            created_by=user.id,
        )
        item_ids.append(item_id)

    inserted = suite_repo.add_items(suite_id=suite.id, item_ids=item_ids)
    listed = suite_repo.list_item_ids(suite.id)

    assert inserted == 3
    assert sorted(str(item_id) for item_id in listed) == sorted(
        str(item_id) for item_id in item_ids
    )


def test_add_items_is_idempotent_and_remove_items(
    session: Session, _ctx: tuple[Team, User, Project]
) -> None:
    team, user, project = _ctx
    repo = SuiteRepo(session)
    suite = repo.create(
        project_id=project.id,
        team_id=team.id,
        name="dedupe",
        description="",
        method="manual",
        suite_metadata={},
        created_by=user.id,
    )
    item_ids = [uuid4(), uuid4()]

    first_count = repo.add_items(suite_id=suite.id, item_ids=item_ids)
    second_count = repo.add_items(suite_id=suite.id, item_ids=item_ids)
    removed_count = repo.remove_items(suite_id=suite.id, item_ids=[item_ids[0]])

    assert first_count == 2
    assert second_count == 0
    assert removed_count == 1
    assert repo.list_item_ids(suite.id) == [item_ids[1]]


def test_list_for_project(session: Session, _ctx: tuple[Team, User, Project]) -> None:
    team, user, project = _ctx
    repo = SuiteRepo(session)
    repo.create(
        project_id=project.id,
        team_id=team.id,
        name="a",
        description="",
        method="manual",
        suite_metadata={},
        created_by=user.id,
    )
    repo.create(
        project_id=project.id,
        team_id=team.id,
        name="b",
        description="",
        method="manual",
        suite_metadata={},
        created_by=user.id,
    )

    suites = repo.list_for_project(project.id)

    assert [suite.name for suite in suites] == ["a", "b"]
