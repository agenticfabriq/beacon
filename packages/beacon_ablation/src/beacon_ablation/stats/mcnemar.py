"""McNemar's exact test for paired binary outcomes."""

from __future__ import annotations

from scipy.stats import binomtest  # type: ignore[import-untyped]


def mcnemar_exact(
    baseline_outcomes: dict[str, bool],
    ablated_outcomes: dict[str, bool],
) -> float:
    """Return the exact two-sided McNemar p-value for paired outcomes."""
    common = sorted(set(baseline_outcomes) & set(ablated_outcomes))
    if not common:
        return 1.0

    baseline_only = 0
    ablated_only = 0
    for task_id in common:
        baseline_passed = baseline_outcomes[task_id]
        ablated_passed = ablated_outcomes[task_id]
        if baseline_passed and not ablated_passed:
            baseline_only += 1
        elif ablated_passed and not baseline_passed:
            ablated_only += 1

    discordant = baseline_only + ablated_only
    if discordant == 0:
        return 1.0

    result = binomtest(
        k=baseline_only,
        n=discordant,
        p=0.5,
        alternative="two-sided",
    )
    return float(result.pvalue)
