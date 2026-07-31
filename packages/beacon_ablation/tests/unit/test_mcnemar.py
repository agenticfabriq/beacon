"""Known-answer tests for McNemar's exact test."""

from __future__ import annotations

import pytest
from beacon_ablation.stats.mcnemar import mcnemar_exact
from scipy.stats import binomtest  # type: ignore[import-untyped]


def _make(
    passes_baseline: list[bool], passes_ablated: list[bool]
) -> tuple[dict[str, bool], dict[str, bool]]:
    baseline = {str(i): value for i, value in enumerate(passes_baseline)}
    ablated = {str(i): value for i, value in enumerate(passes_ablated)}
    return baseline, ablated


def test_no_disagreement_returns_p_one() -> None:
    baseline, ablated = _make([True, True, False, True], [True, True, False, True])

    assert mcnemar_exact(baseline, ablated) == 1.0


def test_no_common_tasks_returns_p_one() -> None:
    assert mcnemar_exact({"x": True}, {"y": False}) == 1.0


def test_strongly_significant() -> None:
    baseline, ablated = _make([True] * 10, [False] * 10)

    p_value = mcnemar_exact(baseline, ablated)
    expected = binomtest(k=10, n=10, p=0.5, alternative="two-sided").pvalue

    assert p_value == pytest.approx(expected)
    assert p_value < 0.01


def test_symmetric_disagreement_returns_p_one() -> None:
    baseline, ablated = _make([True] * 5 + [False] * 5, [False] * 5 + [True] * 5)

    assert mcnemar_exact(baseline, ablated) == pytest.approx(1.0)


def test_matches_scipy_binomtest_on_arbitrary_table() -> None:
    baseline, ablated = _make(
        [True] * 7 + [False] * 3 + [True, False],
        [False] * 7 + [True] * 3 + [True, False],
    )
    expected = binomtest(k=7, n=10, p=0.5, alternative="two-sided").pvalue

    assert mcnemar_exact(baseline, ablated) == pytest.approx(expected)


def test_p_in_unit_interval() -> None:
    for n in range(1, 31):
        baseline_values = [i % 2 == 0 for i in range(n)]
        ablated_values = [i % 3 == 0 for i in range(n)]
        baseline, ablated = _make(baseline_values, ablated_values)

        p_value = mcnemar_exact(baseline, ablated)

        assert 0.0 <= p_value <= 1.0


def test_dummy_layer_separation_significant_at_n_50_k_5() -> None:
    n = 50
    baseline_passes = [i < 36 for i in range(n)]
    ablated_passes = [i < 29 for i in range(n)]
    baseline, ablated = _make(baseline_passes, ablated_passes)

    assert mcnemar_exact(baseline, ablated) < 0.05
