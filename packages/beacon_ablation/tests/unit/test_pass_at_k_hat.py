"""Known-answer tests for pass^k consistency."""

from __future__ import annotations

import math

import pytest
from beacon_ablation.stats.pass_at_k import (
    pass_at_k,
    pass_at_k_hat,
    pass_hat_k_realized,
)


def test_all_pass_returns_one() -> None:
    assert pass_at_k_hat(c=10, n=10, k=1) == 1.0
    assert pass_at_k_hat(c=10, n=10, k=5) == 1.0
    assert pass_at_k_hat(c=10, n=10, k=10) == 1.0


def test_all_fail_returns_zero() -> None:
    assert pass_at_k_hat(c=0, n=10, k=1) == 0.0
    assert pass_at_k_hat(c=0, n=10, k=5) == 0.0


def test_c_less_than_k_returns_zero() -> None:
    assert pass_at_k_hat(c=3, n=10, k=5) == 0.0
    assert pass_at_k_hat(c=0, n=10, k=1) == 0.0


def test_k1_equals_c_over_n() -> None:
    assert pass_at_k_hat(c=3, n=10, k=1) == pytest.approx(0.3)
    assert pass_at_k_hat(c=7, n=10, k=1) == pytest.approx(0.7)


def test_kn_one_only_if_c_equals_n() -> None:
    assert pass_at_k_hat(c=10, n=10, k=10) == 1.0
    assert pass_at_k_hat(c=9, n=10, k=10) == 0.0
    assert pass_at_k_hat(c=5, n=10, k=10) == 0.0


def test_known_value_c5_n10_k3() -> None:
    expected = math.comb(5, 3) / math.comb(10, 3)
    assert pass_at_k_hat(c=5, n=10, k=3) == pytest.approx(expected)


def test_known_value_c8_n10_k5() -> None:
    expected = math.comb(8, 5) / math.comb(10, 5)
    assert pass_at_k_hat(c=8, n=10, k=5) == pytest.approx(expected)


def test_pass_hat_k_le_pass_at_k_spot_checks() -> None:
    cases = [(3, 10, 2), (5, 20, 3), (15, 50, 5), (8, 10, 5)]
    for c, n, k in cases:
        assert pass_at_k_hat(c=c, n=n, k=k) <= pass_at_k(c=c, n=n, k=k) + 1e-12


def test_validation_matches_pass_at_k() -> None:
    with pytest.raises(ValueError, match="k cannot exceed n"):
        pass_at_k_hat(c=5, n=10, k=11)
    with pytest.raises(ValueError):
        pass_at_k_hat(c=-1, n=10, k=5)
    with pytest.raises(ValueError):
        pass_at_k_hat(c=11, n=10, k=5)
    with pytest.raises(ValueError, match="k must be >= 1"):
        pass_at_k_hat(c=5, n=10, k=0)
    with pytest.raises(ValueError, match="n must be >= 1"):
        pass_at_k_hat(c=0, n=0, k=1)


def test_realized_all_pass() -> None:
    assert pass_hat_k_realized([True, True, True], k=3) is True


def test_realized_any_fail_is_false() -> None:
    assert pass_hat_k_realized([True, True, False], k=3) is False


def test_realized_uses_first_k_only() -> None:
    assert pass_hat_k_realized([True, True, False], k=2) is True


def test_realized_too_few_returns_false() -> None:
    assert pass_hat_k_realized([True, True], k=3) is False
