"""Pure-math separability-gain scoring on synthetic outcome matrices."""

from __future__ import annotations

from uuid import uuid4

import pytest
from beacon_registry.errors import InvalidSelectorConfigError
from beacon_registry.selectors.separability_gain import (
    SeparabilityGainSelector,
    _entropy_bits,
    separability_gain,
    top_n_by_separability,
)


class TestEntropyBits:
    def test_all_pass_zero_entropy(self) -> None:
        assert _entropy_bits([1, 1, 1, 1]) == pytest.approx(0.0)

    def test_all_fail_zero_entropy(self) -> None:
        assert _entropy_bits([0, 0, 0]) == pytest.approx(0.0)

    def test_half_half_one_bit(self) -> None:
        assert _entropy_bits([1, 0, 1, 0]) == pytest.approx(1.0)

    def test_empty_returns_zero(self) -> None:
        assert _entropy_bits([]) == 0.0

    def test_single_outcome_zero_entropy(self) -> None:
        assert _entropy_bits([1]) == 0.0
        assert _entropy_bits([0]) == 0.0

    def test_three_quarter_one_quarter(self) -> None:
        assert _entropy_bits([1, 1, 1, 0]) == pytest.approx(0.8113, abs=1e-3)


class TestSeparabilityGain:
    def test_uniform_no_penalty(self) -> None:
        score = separability_gain(outcomes=[1, 0, 1, 0], lambda_difficulty=0.0)
        assert score == pytest.approx(1.0)

    def test_with_difficulty_penalty(self) -> None:
        score = separability_gain(outcomes=[0, 0, 0, 0], lambda_difficulty=0.1)
        assert score == pytest.approx(-0.1)

    def test_all_pass_low_score(self) -> None:
        assert separability_gain(outcomes=[1, 1, 1, 1], lambda_difficulty=0.1) == pytest.approx(0.0)


class TestTopNByGain:
    def test_picks_highest_gain_tasks(self) -> None:
        outcomes_by_task = {
            "t1": [1, 0, 1, 0],
            "t2": [1, 1, 1, 0],
            "t3": [1, 1, 1, 1],
            "t4": [0, 0, 0, 0],
        }

        chosen = top_n_by_separability(
            outcomes_by_task=outcomes_by_task,
            n=2,
            lambda_difficulty=0.1,
        )

        assert chosen == ["t1", "t2"]

    def test_picks_all_when_n_exceeds_pool(self) -> None:
        outcomes_by_task = {"a": [1, 0], "b": [1, 0]}

        chosen = top_n_by_separability(
            outcomes_by_task=outcomes_by_task,
            n=10,
            lambda_difficulty=0.0,
        )

        assert sorted(chosen) == ["a", "b"]

    def test_empty_pool_returns_empty(self) -> None:
        chosen = top_n_by_separability(
            outcomes_by_task={},
            n=50,
            lambda_difficulty=0.1,
        )
        assert chosen == []

    def test_n_zero_returns_empty(self) -> None:
        chosen = top_n_by_separability(
            outcomes_by_task={"a": [1, 0, 1, 0]},
            n=0,
            lambda_difficulty=0.0,
        )
        assert chosen == []

    def test_tie_breaks_by_task_id(self) -> None:
        outcomes_by_task = {"b": [1, 0], "a": [1, 0]}

        chosen = top_n_by_separability(
            outcomes_by_task=outcomes_by_task,
            n=1,
            lambda_difficulty=0.0,
        )

        assert chosen == ["a"]


class TestSeparabilityGainSelector:
    def test_rejects_k_less_than_two(self) -> None:
        with pytest.raises(InvalidSelectorConfigError):
            SeparabilityGainSelector(K=1, N=2, suite="bird")

    def test_score_pool_filters_min_runs_and_returns_uuids(self) -> None:
        best = uuid4()
        too_few = uuid4()
        second = uuid4()
        selector = SeparabilityGainSelector(
            K=4,
            N=2,
            suite="bird",
            lambda_difficulty=0.0,
            min_runs_per_task=3,
        )

        chosen = selector.score_pool(
            outcomes_by_task={
                too_few: [1, 0],
                second: [1, 1, 1, 0],
                best: [1, 0, 1, 0],
            }
        )

        assert chosen == [best, second]
