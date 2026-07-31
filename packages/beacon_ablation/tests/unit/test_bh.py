"""Known-answer tests for Benjamini-Hochberg correction."""

from __future__ import annotations

import numpy as np
import pytest
from beacon_ablation.stats.bh import benjamini_hochberg
from statsmodels.stats.multitest import multipletests  # type: ignore[import-untyped]


def test_empty_returns_empty() -> None:
    assert benjamini_hochberg([]) == []


def test_single_p_value_unchanged() -> None:
    assert benjamini_hochberg([0.03]) == pytest.approx([0.03])


def test_uniform_p_values_unchanged() -> None:
    adjusted = benjamini_hochberg([0.5] * 10)

    for value in adjusted:
        assert value == pytest.approx(0.5)


def test_matches_statsmodels_on_known_vector_1() -> None:
    p_values = [0.001, 0.01, 0.05, 0.5]

    _, expected, _, _ = multipletests(p_values, method="fdr_bh")
    adjusted = benjamini_hochberg(p_values)

    assert np.allclose(adjusted, expected)


def test_matches_statsmodels_on_known_vector_2() -> None:
    p_values = [0.5, 0.05, 0.01, 0.001]

    _, expected, _, _ = multipletests(p_values, method="fdr_bh")
    adjusted = benjamini_hochberg(p_values)

    assert np.allclose(adjusted, expected)


def test_matches_statsmodels_on_known_vector_3() -> None:
    p_values = [0.04, 0.03, 0.02, 0.01]

    _, expected, _, _ = multipletests(p_values, method="fdr_bh")
    adjusted = benjamini_hochberg(p_values)

    assert np.allclose(adjusted, expected)


def test_matches_statsmodels_on_random_vector() -> None:
    rng = np.random.default_rng(42)
    for _ in range(10):
        size = int(rng.integers(2, 30))
        p_values = list(rng.uniform(0, 1, size=size))

        _, expected, _, _ = multipletests(p_values, method="fdr_bh")
        adjusted = benjamini_hochberg(p_values)

        assert np.allclose(adjusted, expected, atol=1e-12)


def test_adjusted_never_less_than_input() -> None:
    p_values = [0.01, 0.02, 0.03, 0.5]
    adjusted = benjamini_hochberg(p_values)

    for raw, value in zip(p_values, adjusted, strict=True):
        assert value >= raw - 1e-12


def test_adjusted_capped_at_one() -> None:
    adjusted = benjamini_hochberg([0.9, 0.8, 0.7])

    for value in adjusted:
        assert value <= 1.0


def test_order_preserved() -> None:
    p_values = [0.01, 0.03, 0.02, 0.5, 0.1]
    adjusted = benjamini_hochberg(p_values)
    paired = sorted(zip(p_values, adjusted, strict=True))
    last_adjusted = -1.0

    for _, value in paired:
        assert value >= last_adjusted - 1e-12
        last_adjusted = value


def test_p_outside_unit_raises() -> None:
    with pytest.raises(ValueError):
        benjamini_hochberg([0.5, 1.5])
    with pytest.raises(ValueError):
        benjamini_hochberg([0.5, -0.1])
