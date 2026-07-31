"""Bootstrap confidence intervals for attribution statistics."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def bootstrap_ci(
    values: Sequence[float],
    *,
    statistic_fn: Callable[[np.ndarray], float] = np.mean,
    n_resamples: int = 10_000,
    confidence: float = 0.95,
    rng: np.random.Generator | None = None,
) -> tuple[float, float]:
    """Compute a percentile bootstrap confidence interval on a 1-D sample."""
    if len(values) == 0:
        raise ValueError("bootstrap_ci: values is empty")
    if not (0.0 < confidence < 1.0):
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    if n_resamples < 1:
        raise ValueError(f"n_resamples must be >= 1, got {n_resamples}")

    generator = rng if rng is not None else np.random.default_rng()
    arr = np.asarray(values, dtype=float)
    n = arr.shape[0]
    idx = generator.integers(0, n, size=(n_resamples, n))
    resamples = arr[idx]

    try:
        stats = statistic_fn(resamples)
        stats_arr = np.asarray(stats, dtype=float)
        if stats_arr.shape != (n_resamples,):
            raise TypeError("statistic_fn did not return one value per resample")
    except TypeError:
        stats_arr = np.empty(n_resamples, dtype=float)
        for i in range(n_resamples):
            stats_arr[i] = float(statistic_fn(resamples[i]))

    alpha = (1.0 - confidence) / 2.0
    lo, hi = np.quantile(stats_arr, [alpha, 1.0 - alpha])
    return float(lo), float(hi)


def bootstrap_paired_ci(
    baseline_outcomes: dict[str, bool],
    ablated_outcomes: dict[str, bool],
    *,
    n_resamples: int = 10_000,
    confidence: float = 0.95,
    rng: np.random.Generator | None = None,
) -> tuple[float, float, float]:
    """Compute a paired percentile bootstrap CI on per-task deltas."""
    if not (0.0 < confidence < 1.0):
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    if n_resamples < 1:
        raise ValueError(f"n_resamples must be >= 1, got {n_resamples}")

    common = sorted(set(baseline_outcomes) & set(ablated_outcomes))
    if not common:
        return 0.0, 0.0, 0.0

    generator = rng if rng is not None else np.random.default_rng()
    baseline_arr = np.array([baseline_outcomes[task_id] for task_id in common], dtype=float)
    ablated_arr = np.array([ablated_outcomes[task_id] for task_id in common], dtype=float)
    deltas = baseline_arr - ablated_arr

    n = deltas.shape[0]
    idx = generator.integers(0, n, size=(n_resamples, n))
    boot = deltas[idx].mean(axis=1)

    alpha = (1.0 - confidence) / 2.0
    lo, hi = np.quantile(boot, [alpha, 1.0 - alpha])
    return float(deltas.mean()), float(lo), float(hi)
