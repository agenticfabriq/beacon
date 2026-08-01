"""Separating infra errors from model failures before computing pass rates."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from beacon_ablation.metrics import (
    gradeable_results,
    min_attempts_per_task,
    restrict_to_k_attempts,
    suite_pass_at_k,
)


@dataclass
class _Row:
    item_id: str
    attempt_idx: int
    outcome: str | None
    tokens_input: int = 0
    tokens_output: int = 0
    runtime_ms: int = 0


def test_gradeable_results_drops_error_attempts() -> None:
    rows = [
        _Row("a", 0, "PASS"),
        _Row("b", 0, "ERROR"),
        _Row("c", 0, "FAIL"),
    ]
    assert [row.item_id for row in gradeable_results(rows)] == ["a", "c"]


def test_gradeable_results_keeps_timeouts() -> None:
    """A TIMEOUT is an outcome of the attempt, not an infra failure to grade."""
    rows = [_Row("a", 0, "TIMEOUT")]
    assert len(gradeable_results(rows)) == 1


def test_item_with_a_later_passing_attempt_survives_an_error() -> None:
    rows = [_Row("a", 0, "ERROR"), _Row("a", 1, "PASS")]
    kept = gradeable_results(rows)
    assert len(kept) == 1
    assert suite_pass_at_k(kept, k=1) == 1.0


def test_fully_errored_item_leaves_the_denominator() -> None:
    """An endpoint outage must not read as a quality regression (B3)."""
    rows = [
        _Row("a", 0, "PASS"),
        _Row("b", 0, "FAIL"),
        _Row("c", 0, "ERROR"),
        _Row("d", 0, "ERROR"),
    ]
    # Counting errors as failures would report 0.25 here.
    assert suite_pass_at_k(gradeable_results(rows), k=1) == 0.5


def test_min_attempts_per_task_is_zero_for_no_results() -> None:
    assert min_attempts_per_task([]) == 0


def test_min_attempts_per_task_uses_the_worst_covered_task() -> None:
    rows = [
        _Row("a", 0, "PASS"),
        _Row("a", 1, "PASS"),
        _Row("a", 2, "PASS"),
        _Row("b", 0, "FAIL"),
    ]
    # pass@3 is not answerable for the suite while 'b' has one attempt.
    assert min_attempts_per_task(rows) == 1


def test_min_attempts_per_task_counts_uniform_coverage() -> None:
    rows = [_Row(item, idx, "PASS") for item in ("a", "b") for idx in range(3)]
    assert min_attempts_per_task(rows) == 3


def test_restrict_to_k_attempts_drops_short_tasks() -> None:
    rows = [
        _Row("a", 0, "PASS"),
        _Row("a", 1, "PASS"),
        _Row("b", 0, "FAIL"),
    ]
    kept = restrict_to_k_attempts(rows, k=2)
    assert {row.item_id for row in kept} == {"a"}


def test_restrict_to_k_attempts_keeps_everything_at_k_1() -> None:
    rows = [_Row("a", 0, "PASS"), _Row("b", 0, "FAIL")]
    assert len(restrict_to_k_attempts(rows, k=1)) == 2


def test_restrict_to_k_attempts_stops_pass_at_k_degrading_silently() -> None:
    """Without the guard, a 1-attempt task answers pass@3 from one attempt."""
    rows = [
        _Row("a", 0, "FAIL"),
        _Row("a", 1, "FAIL"),
        _Row("a", 2, "PASS"),
        _Row("b", 0, "FAIL"),
    ]
    # 'b' would otherwise contribute a pass@3 of False computed from one attempt.
    assert suite_pass_at_k(restrict_to_k_attempts(rows, k=3), k=3) == 1.0


def test_restrict_to_k_attempts_rejects_k_below_one() -> None:
    with pytest.raises(ValueError, match="k must be >= 1"):
        restrict_to_k_attempts([], k=0)
