"""BIRD separability selector tests."""

from __future__ import annotations

import pytest
from beacon_benchmarks.bird_minidev.selector import (
    HistoricalOutcome,
    TaskCandidate,
    select_subset,
    separability_score,
)


def test_separability_score_zero_for_all_pass() -> None:
    score = separability_score([True, True, True, True])
    assert score == pytest.approx(0.0)


def test_separability_score_zero_for_all_fail() -> None:
    score = separability_score([False, False, False])
    assert score == pytest.approx(0.0)


def test_separability_score_maxed_at_50_50() -> None:
    score = separability_score([True, False, True, False])
    assert score == pytest.approx(0.25)


def test_select_subset_returns_n_target() -> None:
    candidates = [
        TaskCandidate(
            task_id=f"t{i}",
            difficulty="simple" if i < 30 else "moderate" if i < 60 else "challenging",
            outcomes=[HistoricalOutcome(run_id=f"r{j}", passed=(i + j) % 2 == 0) for j in range(5)],
        )
        for i in range(100)
    ]

    chosen = select_subset(candidates, n_target=50)

    assert len(chosen) == 50


def test_select_subset_spreads_difficulty() -> None:
    candidates = (
        [TaskCandidate(f"s{i}", "simple", [HistoricalOutcome("r", True)]) for i in range(30)]
        + [TaskCandidate(f"m{i}", "moderate", [HistoricalOutcome("r", False)]) for i in range(30)]
        + [
            TaskCandidate(
                f"c{i}",
                "challenging",
                [HistoricalOutcome(f"r{j}", j % 2 == 0) for j in range(5)],
            )
            for i in range(30)
        ]
    )

    chosen = select_subset(candidates, n_target=30)
    difficulties = {candidate.difficulty for candidate in chosen}

    assert "challenging" in difficulties
    assert len(difficulties) >= 2


def test_select_subset_includes_handpicked() -> None:
    handpicked = ["h1", "h2"]
    candidates = [
        TaskCandidate(task_id, "simple", [HistoricalOutcome("r", True)]) for task_id in handpicked
    ] + [
        TaskCandidate(
            f"t{i}",
            "moderate",
            [HistoricalOutcome(f"r{j}", j % 2 == 0) for j in range(4)],
        )
        for i in range(60)
    ]

    chosen = select_subset(candidates, n_target=10, handpicked_ids=handpicked)
    ids = {candidate.task_id for candidate in chosen}

    assert "h1" in ids and "h2" in ids


def test_select_subset_fewer_candidates_than_target_returns_all() -> None:
    candidates = [
        TaskCandidate(f"t{i}", "simple", [HistoricalOutcome("r", i % 2 == 0)]) for i in range(5)
    ]

    chosen = select_subset(candidates, n_target=50)

    assert len(chosen) == 5
