"""Pure separability-gain scoring and top-N selection."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from beacon_storage.models.runs import Result, Run, VerdictOutcome
from sqlalchemy import select

from beacon_registry.errors import InvalidSelectorConfigError, NoCandidateRunsError
from beacon_registry.items import ItemService
from beacon_registry.suites import SuiteService

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.orm import Session


def _entropy_bits(outcomes: Sequence[int]) -> float:
    """Return Shannon entropy in bits for a Bernoulli outcome sample."""
    sample_size = len(outcomes)
    if sample_size < 2:
        return 0.0

    pass_rate = sum(outcomes) / sample_size
    if pass_rate <= 0.0 or pass_rate >= 1.0:
        return 0.0

    fail_rate = 1.0 - pass_rate
    return -pass_rate * math.log2(pass_rate) - fail_rate * math.log2(fail_rate)


def separability_gain(*, outcomes: Sequence[int], lambda_difficulty: float = 0.1) -> float:
    """Return entropy minus a difficulty penalty for one task."""
    if not outcomes:
        return 0.0

    pass_rate = sum(outcomes) / len(outcomes)
    difficulty = 1.0 - pass_rate
    return _entropy_bits(outcomes) - lambda_difficulty * difficulty


def top_n_by_separability(
    *,
    outcomes_by_task: Mapping[str, Sequence[int]],
    n: int,
    lambda_difficulty: float = 0.1,
) -> list[str]:
    """Pick top task ids by separability gain, with task-id tie-breaks."""
    if n <= 0 or not outcomes_by_task:
        return []

    scored = [
        (
            separability_gain(
                outcomes=outcomes,
                lambda_difficulty=lambda_difficulty,
            ),
            task_id,
        )
        for task_id, outcomes in outcomes_by_task.items()
    ]
    scored.sort(key=lambda scored_task: (-scored_task[0], scored_task[1]))
    return [task_id for _, task_id in scored[:n]]


class SeparabilityGainSelector:
    """Selector wrapper for pre-aggregated separability outcomes."""

    name = "separability_gain"
    version = "v1"

    def __init__(
        self,
        *,
        K: int,
        N: int,
        suite: str,
        lambda_difficulty: float = 0.1,
        min_runs_per_task: int = 3,
    ) -> None:
        if K < 2:
            raise InvalidSelectorConfigError(f"K must be >= 2 to measure separability (got K={K})")
        if N <= 0:
            raise InvalidSelectorConfigError(f"N must be > 0 (got N={N})")

        self.K = K
        self.N = N
        self.suite = suite
        self.lambda_difficulty = lambda_difficulty
        self.min_runs_per_task = min_runs_per_task

    def score_pool(self, *, outcomes_by_task: Mapping[UUID, Sequence[int]]) -> list[UUID]:
        """Return top item ids from pre-aggregated outcomes."""
        eligible = {
            str(item_id): outcomes
            for item_id, outcomes in outcomes_by_task.items()
            if len(outcomes) >= self.min_runs_per_task
        }
        chosen = top_n_by_separability(
            outcomes_by_task=eligible,
            n=self.N,
            lambda_difficulty=self.lambda_difficulty,
        )
        return [UUID(item_id) for item_id in chosen]

    def run_against_history(
        self,
        *,
        session: Session,
        project_id: UUID,
        team_id: UUID,
        created_by: UUID,
        suite_name: str,
    ) -> list[UUID]:
        """Select against historical results, tag items, and write a suite."""
        outcomes_by_task: defaultdict[UUID, list[int]] = defaultdict(list)
        stmt = (
            select(Result.item_id, Result.outcome)
            .join(Run, Run.id == Result.run_id)
            .where(
                Run.team_id == team_id,
                Run.project_id == project_id,
                Run.suite == self.suite,
                Result.project_id == project_id,
                Result.outcome.is_not(None),
            )
        )
        for item_id, outcome in session.execute(stmt):
            try:
                parsed_id = UUID(str(item_id))
            except ValueError:
                continue
            outcome_label = outcome.value if isinstance(outcome, VerdictOutcome) else str(outcome)
            outcomes_by_task[parsed_id].append(
                1 if outcome_label == VerdictOutcome.PASS.value else 0
            )

        if not outcomes_by_task:
            raise NoCandidateRunsError(
                f"no historical Result rows found for team {team_id} and suite '{self.suite}'"
            )

        chosen = self.score_pool(outcomes_by_task=outcomes_by_task)
        if not chosen:
            raise NoCandidateRunsError(
                f"no tasks met min_runs_per_task={self.min_runs_per_task}; "
                f"K should be >= {self.min_runs_per_task} for this suite"
            )

        subset_tag = f"curated_{self.N}_{datetime.now(UTC).strftime('%Y-%m-%d')}"
        item_service = ItemService(session)
        for item_id in chosen:
            active = item_service.get_active(item_id)
            if active is None:
                continue
            item_metadata = dict(active.item_metadata or {})
            item_metadata["subset_tag"] = subset_tag
            item_metadata["selector"] = {
                "name": self.name,
                "version": self.version,
                "K": self.K,
                "N": self.N,
                "lambda_difficulty": self.lambda_difficulty,
            }
            item_service.update_item(
                item_id=item_id,
                tier=active.tier,
                suite=active.suite,
                team_id=active.team_id,
                solution_id=active.solution_id,
                dataset_version=active.dataset_version,
                item_input=active.item_input,
                gold_answer=active.gold_answer,
                item_metadata=item_metadata,
                created_by=created_by,
            )

        suite_service = SuiteService(session)
        suite = suite_service.get_by_project_and_name(project_id=project_id, name=suite_name)
        if suite is None:
            suite = suite_service.create(
                project_id=project_id,
                team_id=team_id,
                name=suite_name,
                description=f"separability-curated {self.N}",
                method="separability_gain",
                suite_metadata={
                    "subset_tag": subset_tag,
                    "K": self.K,
                    "N": self.N,
                    "lambda_difficulty": self.lambda_difficulty,
                },
                created_by=created_by,
            )
        suite_service.replace_items(suite_id=suite.id, item_ids=chosen)
        return chosen
