"""SeparabilityGainSelector DB integration and suite writes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_registry.errors import NoCandidateRunsError
from beacon_registry.items import ItemService
from beacon_registry.selectors.separability_gain import SeparabilityGainSelector
from beacon_registry.suites import SuiteService
from beacon_registry.types import EvalItemTier
from beacon_storage.models.runs import HarnessMode, ResultStatus, VerdictOutcome
from beacon_storage.models.tenancy import Project, Team, User
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


pytestmark = pytest.mark.integration


@pytest.fixture
def _ctx(session: Session) -> tuple[Team, User, Project]:
    team = Team(name="sel-team")
    user = User(email="sel@example.com", name="S")
    session.add_all([team, user])
    session.flush()
    project = Project(team_id=team.id, name="sel", created_by=user.id)
    session.add(project)
    session.flush()
    return team, user, project


def _create_items(
    session: Session,
    *,
    team_id: UUID,
    user_id: UUID,
    count: int,
) -> list[UUID]:
    item_service = ItemService(session)
    return [
        item_service.create_item(
            tier=EvalItemTier.HUMAN_VERIFIED,
            suite="bird",
            team_id=team_id,
            solution_id=None,
            dataset_version="v",
            item_input={"i": index},
            gold_answer={"a": "x"},
            item_metadata={},
            created_by=user_id,
        )
        for index in range(count)
    ]


def _seed_history(
    session: Session,
    *,
    team_id: UUID,
    user_id: UUID,
    project_id: UUID,
    item_ids: list[UUID],
    outcome_grid: list[list[int]],
) -> None:
    """Create one run per config column and one result per config/item pair."""
    solution = SolutionRepo(session).create(
        team_id=team_id,
        solution_id="dummy",
        version="0.2",
        owner_team=team_id,
        summary="",
        supported_modes=["EVAL"],
        layers=[],
        created_by=user_id,
    )
    for config_index in range(len(outcome_grid[0])):
        run = RunRepo(session).create(
            team_id=team_id,
            project_id=project_id,
            solution_id=solution.id,
            suite="bird",
            dataset_version="v",
            mode=HarnessMode.EVAL,
            pass_idx=config_index,
            config={"variant": config_index},
            created_by=user_id,
        )
        for item_index, item_id in enumerate(item_ids):
            outcome = outcome_grid[item_index][config_index]
            ResultRepo(session).create(
                team_id=team_id,
                project_id=project_id,
                run_id=run.id,
                item_id=str(item_id),
                attempt_idx=0,
                output={"answer": "x"},
                output_kind="answer",
                tokens_input=1,
                tokens_output=1,
                runtime_ms=1,
                status=ResultStatus.COMPLETED,
                outcome=VerdictOutcome.PASS if outcome else VerdictOutcome.FAIL,
                error=None,
            )
    session.commit()


def test_run_against_history_picks_half_half_tasks(
    session: Session,
    _ctx: tuple[Team, User, Project],
) -> None:
    team, user, project = _ctx
    item_ids = _create_items(session, team_id=team.id, user_id=user.id, count=10)
    session.commit()
    outcome_grid = (
        [[1, 0, 1] for _ in range(4)]
        + [[1, 1, 1] for _ in range(3)]
        + [[0, 0, 0] for _ in range(3)]
    )
    _seed_history(
        session,
        team_id=team.id,
        user_id=user.id,
        project_id=project.id,
        item_ids=item_ids,
        outcome_grid=outcome_grid,
    )

    selector = SeparabilityGainSelector(
        K=3,
        N=4,
        suite="bird",
        lambda_difficulty=0.1,
        min_runs_per_task=3,
    )
    chosen = selector.run_against_history(
        session=session,
        project_id=project.id,
        team_id=team.id,
        created_by=user.id,
        suite_name="curated-4-2026-06-05",
    )

    assert sorted(chosen) == sorted(item_ids[:4])
    suite = SuiteService(session).get_by_project_and_name_or_raise(
        project_id=project.id,
        name="curated-4-2026-06-05",
    )
    assert sorted(SuiteService(session).list_item_ids(suite.id)) == sorted(chosen)

    item_service = ItemService(session)
    for item_id in chosen:
        active = item_service.get_active(item_id)
        assert active is not None
        assert active.item_metadata["subset_tag"].startswith("curated_4_")
        assert active.item_metadata["selector"]["name"] == "separability_gain"


def test_run_against_history_raises_if_no_candidate_runs(
    session: Session,
    _ctx: tuple[Team, User, Project],
) -> None:
    team, user, project = _ctx
    selector = SeparabilityGainSelector(
        K=3,
        N=4,
        suite="bird-empty",
        lambda_difficulty=0.1,
    )

    with pytest.raises(NoCandidateRunsError):
        selector.run_against_history(
            session=session,
            project_id=project.id,
            team_id=team.id,
            created_by=user.id,
            suite_name="x",
        )
