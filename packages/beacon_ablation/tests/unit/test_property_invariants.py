"""Property-based tests for Beacon's statistical primitives."""

from __future__ import annotations

import numpy as np
import pytest
from beacon_ablation.stats import (
    benjamini_hochberg,
    bootstrap_ci,
    bootstrap_paired_ci,
    mcnemar_exact,
    pass_at_k,
    pass_at_k_hat,
)
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

_N_MAX = 200


@st.composite
def _c_n_k(draw: st.DrawFn) -> tuple[int, int, int]:
    n = draw(st.integers(min_value=1, max_value=_N_MAX))
    c = draw(st.integers(min_value=0, max_value=n))
    k = draw(st.integers(min_value=1, max_value=n))
    return c, n, k


_bounded_floats = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)


@given(_c_n_k())
def test_pass_at_k_in_unit_interval(cnk: tuple[int, int, int]) -> None:
    c, n, k = cnk

    value = pass_at_k(c=c, n=n, k=k)

    assert 0.0 <= value <= 1.0


@given(_c_n_k())
def test_pass_at_k_k_one_equals_c_over_n(cnk: tuple[int, int, int]) -> None:
    c, n, _ = cnk

    assert pass_at_k(c=c, n=n, k=1) == pytest.approx(c / n)


@given(_c_n_k())
def test_pass_at_k_monotone_in_k(cnk: tuple[int, int, int]) -> None:
    c, n, _ = cnk
    previous = 0.0

    for k in range(1, n + 1):
        value = pass_at_k(c=c, n=n, k=k)
        assert value >= previous - 1e-12
        previous = value


@given(_c_n_k())
def test_pass_at_k_hat_in_unit_interval(cnk: tuple[int, int, int]) -> None:
    c, n, k = cnk

    value = pass_at_k_hat(c=c, n=n, k=k)

    assert 0.0 <= value <= 1.0


@given(_c_n_k())
def test_pass_hat_k_le_pass_at_k(cnk: tuple[int, int, int]) -> None:
    c, n, k = cnk

    pass_hat = pass_at_k_hat(c=c, n=n, k=k)
    pass_at = pass_at_k(c=c, n=n, k=k)

    assert pass_hat <= pass_at + 1e-12


@given(_c_n_k())
def test_pass_at_k_hat_monotone_decreasing_in_k(cnk: tuple[int, int, int]) -> None:
    c, n, _ = cnk
    previous = 1.0

    for k in range(1, n + 1):
        value = pass_at_k_hat(c=c, n=n, k=k)
        assert value <= previous + 1e-12
        previous = value


@given(st.lists(_bounded_floats, min_size=2, max_size=100))
@settings(
    deadline=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_bootstrap_ci_brackets_sample_mean(values: list[float]) -> None:
    rng = np.random.default_rng(42)
    sample_mean = float(np.mean(values))

    lo, hi = bootstrap_ci(values, statistic_fn=np.mean, n_resamples=500, rng=rng)

    assert lo - 1e-9 <= sample_mean <= hi + 1e-9


@given(st.lists(_bounded_floats, min_size=2, max_size=100))
@settings(
    deadline=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_bootstrap_ci_within_input_range(values: list[float]) -> None:
    rng = np.random.default_rng(42)

    lo, hi = bootstrap_ci(values, statistic_fn=np.mean, n_resamples=500, rng=rng)

    assert min(values) - 1e-9 <= lo
    assert hi <= max(values) + 1e-9


@given(st.integers(min_value=2, max_value=50))
@settings(deadline=None)
def test_paired_bootstrap_returns_zero_delta_when_outcomes_match(n: int) -> None:
    rng = np.random.default_rng(42)
    common = {f"t{i}": True for i in range(n)}

    delta, lo, hi = bootstrap_paired_ci(common, common, n_resamples=200, rng=rng)

    assert delta == 0.0
    assert lo == 0.0
    assert hi == 0.0


@given(st.dictionaries(st.text(min_size=1, max_size=8), st.booleans(), min_size=1, max_size=30))
@settings(deadline=None)
def test_mcnemar_p_in_unit_interval(outcomes: dict[str, bool]) -> None:
    p_value = mcnemar_exact(outcomes, outcomes)

    assert 0.0 <= p_value <= 1.0


@given(st.dictionaries(st.text(min_size=1, max_size=8), st.booleans(), min_size=1, max_size=30))
@settings(deadline=None)
def test_mcnemar_identical_outcomes_returns_one(outcomes: dict[str, bool]) -> None:
    p_value = mcnemar_exact(outcomes, outcomes)

    assert p_value == 1.0


@given(st.lists(_bounded_floats, min_size=1, max_size=50))
@example(p_values=[0.5] * 10)
@example(p_values=[0.0])
@example(p_values=[1.0])
def test_bh_adjusted_never_less_than_raw(p_values: list[float]) -> None:
    adjusted = benjamini_hochberg(p_values)

    for raw, value in zip(p_values, adjusted, strict=True):
        assert value >= raw - 1e-12


@given(st.lists(_bounded_floats, min_size=1, max_size=50))
def test_bh_adjusted_in_unit_interval(p_values: list[float]) -> None:
    adjusted = benjamini_hochberg(p_values)

    for value in adjusted:
        assert 0.0 <= value <= 1.0


@given(st.lists(_bounded_floats, min_size=1, max_size=50))
def test_bh_order_preserving(p_values: list[float]) -> None:
    adjusted = benjamini_hochberg(p_values)
    paired = sorted(zip(p_values, adjusted, strict=True))
    previous = -1.0

    for _, value in paired:
        assert value >= previous - 1e-12
        previous = value


@given(
    _bounded_floats,
    st.integers(min_value=1, max_value=20),
)
def test_bh_uniform_idempotent(p_value: float, m: int) -> None:
    adjusted = benjamini_hochberg([p_value] * m)

    for value in adjusted:
        assert value == pytest.approx(p_value)
