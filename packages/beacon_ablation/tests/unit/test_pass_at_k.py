"""Known-answer tests for pass_at_k."""

from __future__ import annotations

import math

import pytest
from beacon_ablation.stats.pass_at_k import pass_at_k


def test_all_pass_returns_one() -> None:
    assert pass_at_k(c=10, n=10, k=1) == 1.0
    assert pass_at_k(c=10, n=10, k=5) == 1.0
    assert pass_at_k(c=10, n=10, k=10) == 1.0


def test_all_fail_returns_zero() -> None:
    assert pass_at_k(c=0, n=10, k=1) == 0.0
    assert pass_at_k(c=0, n=10, k=5) == 0.0
    assert pass_at_k(c=0, n=10, k=10) == 0.0


def test_k1_equals_c_over_n() -> None:
    assert pass_at_k(c=1, n=10, k=1) == pytest.approx(0.1)
    assert pass_at_k(c=3, n=10, k=1) == pytest.approx(0.3)
    assert pass_at_k(c=7, n=10, k=1) == pytest.approx(0.7)


def test_kn_one_if_any_pass() -> None:
    assert pass_at_k(c=1, n=10, k=10) == pytest.approx(1.0)
    assert pass_at_k(c=5, n=10, k=10) == pytest.approx(1.0)


def test_known_value_c1_n10_k5() -> None:
    assert pass_at_k(c=1, n=10, k=5) == pytest.approx(0.5)


def test_known_value_c2_n10_k5() -> None:
    expected = 1 - math.comb(8, 5) / math.comb(10, 5)
    assert pass_at_k(c=2, n=10, k=5) == pytest.approx(expected)


def test_known_value_c3_n100_k10() -> None:
    expected = 1 - math.comb(97, 10) / math.comb(100, 10)
    assert pass_at_k(c=3, n=100, k=10) == pytest.approx(expected, rel=1e-9)


def test_large_n_does_not_overflow() -> None:
    p = pass_at_k(c=100, n=10000, k=50)

    assert 0.0 <= p <= 1.0
    assert 0.30 < p < 0.50


def test_k_greater_than_n_raises() -> None:
    with pytest.raises(ValueError, match="k cannot exceed n"):
        pass_at_k(c=5, n=10, k=11)


def test_negative_c_raises() -> None:
    with pytest.raises(ValueError):
        pass_at_k(c=-1, n=10, k=5)


def test_c_greater_than_n_raises() -> None:
    with pytest.raises(ValueError):
        pass_at_k(c=11, n=10, k=5)


def test_k_zero_raises() -> None:
    with pytest.raises(ValueError, match="k must be >= 1"):
        pass_at_k(c=5, n=10, k=0)


def test_n_zero_raises() -> None:
    with pytest.raises(ValueError, match="n must be >= 1"):
        pass_at_k(c=0, n=0, k=1)
