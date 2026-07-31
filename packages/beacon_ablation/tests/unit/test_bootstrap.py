"""Known-answer and invariant tests for bootstrap confidence intervals."""

from __future__ import annotations

import numpy as np
import pytest
from beacon_ablation.stats.bootstrap import bootstrap_ci, bootstrap_paired_ci


def test_bootstrap_is_deterministic_given_seed() -> None:
    rng1 = np.random.default_rng(42)
    rng2 = np.random.default_rng(42)
    values = [0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]

    lo1, hi1 = bootstrap_ci(values, statistic_fn=np.mean, n_resamples=1000, rng=rng1)
    lo2, hi2 = bootstrap_ci(values, statistic_fn=np.mean, n_resamples=1000, rng=rng2)

    assert lo1 == lo2
    assert hi1 == hi2


def test_bootstrap_of_constant_vector() -> None:
    rng = np.random.default_rng(42)

    lo, hi = bootstrap_ci([1.0] * 100, statistic_fn=np.mean, n_resamples=1000, rng=rng)

    assert lo == pytest.approx(1.0)
    assert hi == pytest.approx(1.0)


def test_bootstrap_of_alternating_zero_one() -> None:
    rng = np.random.default_rng(42)
    values = [0.0] * 500 + [1.0] * 500

    lo, hi = bootstrap_ci(values, statistic_fn=np.mean, n_resamples=5000, rng=rng)

    assert lo < 0.5 < hi
    assert (hi - lo) < 0.1


def test_bootstrap_ci_brackets_sample_mean() -> None:
    rng = np.random.default_rng(42)
    values = [0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0]
    sample_mean = float(np.mean(values))

    lo, hi = bootstrap_ci(values, statistic_fn=np.mean, n_resamples=2000, rng=rng)

    assert lo - 1e-9 <= sample_mean <= hi + 1e-9


def test_ci_tightens_with_larger_n() -> None:
    rng = np.random.default_rng(42)
    small = bootstrap_ci([0, 1] * 25, statistic_fn=np.mean, n_resamples=2000, rng=rng)
    rng = np.random.default_rng(42)
    large = bootstrap_ci([0, 1] * 250, statistic_fn=np.mean, n_resamples=2000, rng=rng)

    assert (large[1] - large[0]) < (small[1] - small[0])


def test_90_pct_ci_narrower_than_95_pct() -> None:
    rng1 = np.random.default_rng(42)
    rng2 = np.random.default_rng(42)
    values = list(np.random.default_rng(0).integers(0, 2, size=200).astype(float))

    lo90, hi90 = bootstrap_ci(
        values, statistic_fn=np.mean, n_resamples=2000, confidence=0.90, rng=rng1
    )
    lo95, hi95 = bootstrap_ci(
        values, statistic_fn=np.mean, n_resamples=2000, confidence=0.95, rng=rng2
    )

    assert (hi95 - lo95) >= (hi90 - lo90)


def test_paired_ci_recovers_known_delta() -> None:
    rng = np.random.default_rng(42)
    baseline = {f"t{i}": (i < 75) for i in range(100)}
    ablated = {f"t{i}": (i < 25) for i in range(100)}

    delta, lo, hi = bootstrap_paired_ci(baseline, ablated, n_resamples=2000, rng=rng)

    assert delta == pytest.approx(0.5)
    assert lo < 0.5 < hi


def test_paired_ci_returns_zero_on_identical_outcomes() -> None:
    rng = np.random.default_rng(42)
    baseline = {f"t{i}": True for i in range(50)}
    ablated = {f"t{i}": True for i in range(50)}

    delta, lo, hi = bootstrap_paired_ci(baseline, ablated, n_resamples=500, rng=rng)

    assert delta == 0.0
    assert lo == 0.0
    assert hi == 0.0


def test_paired_ci_only_uses_common_tasks() -> None:
    rng = np.random.default_rng(42)
    baseline = {"t1": True, "t2": True, "t3": True, "unique_to_baseline": True}
    ablated = {"t1": False, "t2": False, "t3": False, "unique_to_ablated": True}

    delta, _, _ = bootstrap_paired_ci(baseline, ablated, n_resamples=500, rng=rng)

    assert delta == pytest.approx(1.0)


def test_paired_ci_no_common_tasks_returns_zero() -> None:
    rng = np.random.default_rng(42)

    delta, lo, hi = bootstrap_paired_ci({"a": True}, {"b": True}, n_resamples=500, rng=rng)

    assert delta == 0.0
    assert lo == 0.0
    assert hi == 0.0


def test_empty_values_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        bootstrap_ci([], statistic_fn=np.mean, n_resamples=100)


def test_invalid_confidence_raises() -> None:
    with pytest.raises(ValueError):
        bootstrap_ci([1.0, 2.0], statistic_fn=np.mean, confidence=1.5)
    with pytest.raises(ValueError):
        bootstrap_ci([1.0, 2.0], statistic_fn=np.mean, confidence=0.0)


def test_invalid_n_resamples_raises() -> None:
    with pytest.raises(ValueError):
        bootstrap_ci([1.0, 2.0], statistic_fn=np.mean, n_resamples=0)
