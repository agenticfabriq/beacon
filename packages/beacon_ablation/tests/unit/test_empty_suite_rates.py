"""A rate over no tasks is unknown, not zero (B15).

``suite_pass_at_k`` returned 0.0 for an empty task set, so an arm wiped out by
an endpoint outage was recorded as having *scored zero* rather than as having
produced nothing. Observed live: a sweep whose ablated arms all errored stored
``pass_at_k_ablated = 0.0`` while the run API correctly reported ``null``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from beacon_ablation.metrics import (
    gradeable_results,
    suite_pass_at_k,
    suite_pass_hat_k,
)


@dataclass
class _Row:
    item_id: str
    attempt_idx: int
    outcome: str


@pytest.mark.parametrize("rate", [suite_pass_at_k, suite_pass_hat_k])
def test_no_tasks_is_unknown(rate: object) -> None:
    assert rate([], k=1) is None  # type: ignore[operator]


@pytest.mark.parametrize("rate", [suite_pass_at_k, suite_pass_hat_k])
def test_an_all_errored_arm_is_unknown_not_zero(rate: object) -> None:
    """The live failure: every attempt errored, so nothing was gradeable."""
    rows = [_Row(f"i-{i}", 0, "ERROR") for i in range(5)]

    assert rate(gradeable_results(rows), k=1) is None  # type: ignore[operator]


def test_a_genuine_zero_is_still_zero() -> None:
    """An arm that was graded and failed everything must stay 0.0, not None."""
    rows = [_Row(f"i-{i}", 0, "FAIL") for i in range(5)]

    assert suite_pass_at_k(rows, k=1) == 0.0
    assert suite_pass_hat_k(rows, k=1) == 0.0


def test_a_normal_rate_is_unaffected() -> None:
    rows = [_Row("a", 0, "PASS"), _Row("b", 0, "FAIL")]

    assert suite_pass_at_k(rows, k=1) == 0.5


def test_zero_and_unknown_are_distinguishable() -> None:
    """The whole point: these two conditions must not produce the same value."""
    graded_all_failed = [_Row("a", 0, "FAIL")]
    nothing_gradeable = gradeable_results([_Row("a", 0, "ERROR")])

    assert suite_pass_at_k(graded_all_failed, k=1) == 0.0
    assert suite_pass_at_k(nothing_gradeable, k=1) is None
