"""HumanEval pass@k estimator.

Given n independent attempts on a task and c passing attempts, pass@k estimates
the probability that k randomly selected attempts contain at least one pass:

    pass@k = 1 - C(n - c, k) / C(n, k)

The product form below avoids constructing large combinations:

    pass@k = 1 - prod((n - c - i) / (n - i) for i in range(k))
"""

from __future__ import annotations


def pass_at_k(*, c: int, n: int, k: int) -> float:
    """Estimate pass@k using the HumanEval formula."""
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    if k > n:
        raise ValueError(f"k cannot exceed n; got k={k}, n={n}")
    if c < 0:
        raise ValueError(f"c must be >= 0, got {c}")
    if c > n:
        raise ValueError(f"c cannot exceed n; got c={c}, n={n}")

    if c == 0:
        return 0.0
    if c == n or n - c < k:
        return 1.0

    product = 1.0
    for i in range(k):
        product *= (n - c - i) / (n - i)
    return 1.0 - product


def pass_at_k_hat(*, c: int, n: int, k: int) -> float:
    """Estimate pass^k: probability that k selected attempts are all passes."""
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    if k > n:
        raise ValueError(f"k cannot exceed n; got k={k}, n={n}")
    if c < 0:
        raise ValueError(f"c must be >= 0, got {c}")
    if c > n:
        raise ValueError(f"c cannot exceed n; got c={c}, n={n}")

    if c < k:
        return 0.0
    if c == n:
        return 1.0

    product = 1.0
    for i in range(k):
        product *= (c - i) / (n - i)
    return product


def pass_hat_k_realized(outcomes: list[bool], k: int) -> bool:
    """Return True iff the first k attempts all passed."""
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    if len(outcomes) < k:
        return False
    return all(outcomes[:k])
