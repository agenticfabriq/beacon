"""Separability-gain subset selector for BIRD Mini-Dev."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True)
class HistoricalOutcome:
    """Historical pass/fail outcome for one task in one run."""

    run_id: str
    passed: bool


@dataclass
class TaskCandidate:
    """Candidate BIRD task with historical outcomes."""

    task_id: str
    difficulty: str | None
    outcomes: list[HistoricalOutcome]


def separability_score(outcomes: Iterable[bool]) -> float:
    """Return Bernoulli variance of historical pass/fail outcomes."""
    values = list(outcomes)
    if not values:
        return 0.0
    pass_rate = sum(1 for value in values if value) / len(values)
    return pass_rate * (1.0 - pass_rate)


def _score(candidate: TaskCandidate) -> float:
    return separability_score(outcome.passed for outcome in candidate.outcomes)


def _rank_key(candidate: TaskCandidate) -> tuple[float, str]:
    return (-_score(candidate), candidate.task_id)


def select_subset(
    candidates: list[TaskCandidate],
    *,
    n_target: int,
    handpicked_ids: list[str] | None = None,
) -> list[TaskCandidate]:
    """Pick at most ``n_target`` tasks by stratified separability ranking."""
    if n_target <= 0:
        return []
    if len(candidates) <= n_target:
        return list(candidates)

    by_id = {candidate.task_id: candidate for candidate in candidates}
    requested = handpicked_ids or []
    handpicked = [by_id[task_id] for task_id in requested if task_id in by_id][:n_target]
    handpicked_set = {candidate.task_id for candidate in handpicked}
    remaining = [candidate for candidate in candidates if candidate.task_id not in handpicked_set]

    slots_left = n_target - len(handpicked)
    if slots_left <= 0 or not remaining:
        return handpicked

    strata: dict[str, list[TaskCandidate]] = defaultdict(list)
    for candidate in remaining:
        strata[candidate.difficulty or "unknown"].append(candidate)

    sizes = {difficulty: len(group) for difficulty, group in strata.items()}
    total = sum(sizes.values())
    allocation: dict[str, int] = {}
    used = 0
    for difficulty, size in sizes.items():
        share = round(slots_left * size / total)
        allocation[difficulty] = min(share, size)
        used += allocation[difficulty]

    deficit = slots_left - used
    difficulty_order = sorted(strata, key=lambda item: (allocation[item], -sizes[item], item))
    index = 0
    while deficit > 0 and index < len(difficulty_order) * 10:
        difficulty = difficulty_order[index % len(difficulty_order)]
        if allocation[difficulty] < sizes[difficulty]:
            allocation[difficulty] += 1
            deficit -= 1
        index += 1

    chosen = list(handpicked)
    overflow_pool: list[TaskCandidate] = []
    for difficulty, group in strata.items():
        ranked = sorted(group, key=_rank_key)
        chosen.extend(ranked[: allocation[difficulty]])
        overflow_pool.extend(ranked[allocation[difficulty] :])

    if len(chosen) < n_target:
        chosen.extend(sorted(overflow_pool, key=_rank_key)[: n_target - len(chosen)])

    return chosen[:n_target]
