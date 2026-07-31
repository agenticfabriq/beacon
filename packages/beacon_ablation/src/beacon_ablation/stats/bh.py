"""Benjamini-Hochberg false-discovery-rate correction."""

from __future__ import annotations

import numpy as np


def benjamini_hochberg(p_values: list[float], alpha: float = 0.05) -> list[float]:
    """Return BH-adjusted p-values in the same order as input."""
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    if not p_values:
        return []

    p = np.asarray(p_values, dtype=float)
    if np.any(p < 0.0) or np.any(p > 1.0):
        raise ValueError("all p-values must be in [0.0, 1.0]")

    m = p.shape[0]
    order = np.argsort(p, kind="stable")
    ranked = p[order]
    adjusted_sorted = np.empty(m, dtype=float)
    cumulative_min = 1.0

    for i in range(m - 1, -1, -1):
        candidate = ranked[i] * m / (i + 1)
        if candidate < cumulative_min:
            cumulative_min = candidate
        adjusted_sorted[i] = cumulative_min

    np.clip(adjusted_sorted, 0.0, 1.0, out=adjusted_sorted)
    adjusted = np.empty(m, dtype=float)
    adjusted[order] = adjusted_sorted
    return adjusted.tolist()
