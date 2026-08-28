"""Aggregation tests: results rows -> per-task booleans and suite-level rates."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from beacon_ablation.metrics import (
    median_runtime_ms,
    median_total_tokens,
    per_task_pass_at_k,
    per_task_pass_hat_k,
    suite_pass_at_k,
    suite_pass_hat_k,
)


@dataclass
class _FakeResult:
    """Stand-in for the plan's result row shape."""

    task_id: str
    attempt_idx: int
    verdict: str
    tokens_input: int | None = None
    tokens_output: int | None = None
    runtime_ms: int = 0


@dataclass
class _FakePersistedResult:
    """Stand-in for the persisted P2 SQLAlchemy Result row shape."""

    item_id: str
    attempt_idx: int
    outcome: str | None
    tokens_input: int = 0
    tokens_output: int = 0
    runtime_ms: int = 0


def _make(task_id: str, outcomes: list[str]) -> list[_FakeResult]:
    return [
        _FakeResult(task_id=task_id, attempt_idx=i, verdict=outcome)
        for i, outcome in enumerate(outcomes)
    ]


def test_per_task_pass_at_k_all_pass() -> None:
    results = _make("t1", ["PASS", "PASS", "PASS"])

    assert per_task_pass_at_k(results, k=3) == {"t1": True}


def test_per_task_pass_at_k_any_pass_within_k() -> None:
    results = _make("t1", ["FAIL", "FAIL", "PASS"])

    assert per_task_pass_at_k(results, k=3) == {"t1": True}


def test_per_task_pass_at_k_no_pass_within_k() -> None:
    results = _make("t1", ["FAIL", "FAIL", "PASS"])

    assert per_task_pass_at_k(results, k=2) == {"t1": False}


def test_per_task_pass_at_k_error_counts_as_fail() -> None:
    results = _make("t1", ["ERROR", "TIMEOUT"])

    assert per_task_pass_at_k(results, k=2) == {"t1": False}


def test_per_task_pass_at_k_groups_multiple_tasks() -> None:
    results = _make("t1", ["PASS"]) + _make("t2", ["FAIL"])

    assert per_task_pass_at_k(results, k=1) == {"t1": True, "t2": False}


def test_per_task_pass_hat_k_all_pass() -> None:
    results = _make("t1", ["PASS", "PASS", "PASS"])

    assert per_task_pass_hat_k(results, k=3) == {"t1": True}


def test_per_task_pass_hat_k_any_fail_is_false() -> None:
    results = _make("t1", ["PASS", "PASS", "FAIL"])

    assert per_task_pass_hat_k(results, k=3) == {"t1": False}


def test_per_task_pass_hat_k_uses_first_k() -> None:
    results = _make("t1", ["PASS", "PASS", "FAIL", "PASS"])

    assert per_task_pass_hat_k(results, k=2) == {"t1": True}


def test_per_task_pass_hat_k_insufficient_attempts_is_false() -> None:
    results = _make("t1", ["PASS", "PASS"])

    assert per_task_pass_hat_k(results, k=3) == {"t1": False}


def test_suite_pass_at_k_averages_across_tasks() -> None:
    results = (
        _make("t1", ["PASS"])
        + _make("t2", ["PASS"])
        + _make("t3", ["FAIL"])
        + _make("t4", ["FAIL"])
    )

    assert suite_pass_at_k(results, k=1) == pytest.approx(0.5)


def test_suite_pass_hat_k_averages_across_tasks() -> None:
    results = (
        _make("t1", ["PASS", "PASS"])
        + _make("t2", ["PASS", "FAIL"])
        + _make("t3", ["FAIL", "FAIL"])
    )

    assert suite_pass_hat_k(results, k=2) == pytest.approx(1 / 3)


def test_suite_pass_at_k_empty_is_unknown() -> None:
    """No tasks means the rate is undefined, not zero (B15)."""
    assert suite_pass_at_k([], k=1) is None


def test_suite_pass_hat_k_empty_is_unknown() -> None:
    assert suite_pass_hat_k([], k=1) is None


def test_median_total_tokens() -> None:
    results = [
        _FakeResult("t1", 0, "PASS", tokens_input=100, tokens_output=50),
        _FakeResult("t2", 0, "PASS", tokens_input=200, tokens_output=100),
        _FakeResult("t3", 0, "PASS", tokens_input=150, tokens_output=75),
    ]

    assert median_total_tokens(results) == 225.0


def test_median_total_tokens_empty() -> None:
    assert median_total_tokens([]) is None


def test_a_result_with_unrecorded_tokens_is_not_a_result_that_cost_nothing() -> None:
    """Unmeasured cost used to be stored as 0, which drags the median down."""
    results = [
        _FakeResult("t1", 0, "PASS", tokens_input=100, tokens_output=50),
        _FakeResult("t2", 0, "PASS", tokens_input=None, tokens_output=None),
        _FakeResult("t3", 0, "PASS", tokens_input=200, tokens_output=100),
    ]

    assert median_total_tokens(results) == 225.0


def test_half_a_measurement_is_not_a_total() -> None:
    """Output tokens with input unrecorded understates the total, badly.

    The in-process SUT reports one number and it is the output half; calling
    that the total claims the prompt was free, and for a schema-carrying SQL
    prompt the input half is usually the larger one.
    """
    results = [_FakeResult("t1", 0, "PASS", tokens_input=None, tokens_output=9000)]

    assert median_total_tokens(results) is None


def test_no_result_recorded_its_tokens_is_unknown_not_zero() -> None:
    results = [_FakeResult("t1", 0, "PASS"), _FakeResult("t2", 0, "PASS")]

    assert median_total_tokens(results) is None


def test_median_runtime_ms() -> None:
    results = [
        _FakeResult("t1", 0, "PASS", runtime_ms=100),
        _FakeResult("t2", 0, "PASS", runtime_ms=300),
        _FakeResult("t3", 0, "PASS", runtime_ms=200),
    ]

    assert median_runtime_ms(results) == 200.0


def test_median_runtime_ms_empty() -> None:
    assert median_runtime_ms([]) is None


def test_per_task_pass_at_k_orders_by_attempt_idx() -> None:
    results = [
        _FakeResult("t1", attempt_idx=2, verdict="PASS"),
        _FakeResult("t1", attempt_idx=0, verdict="FAIL"),
        _FakeResult("t1", attempt_idx=1, verdict="FAIL"),
    ]

    assert per_task_pass_at_k(results, k=2) == {"t1": False}
    assert per_task_pass_at_k(results, k=3) == {"t1": True}


def test_metrics_accept_persisted_result_item_id_and_outcome_shape() -> None:
    results = [
        _FakePersistedResult(item_id="t1", attempt_idx=0, outcome="FAIL"),
        _FakePersistedResult(item_id="t1", attempt_idx=1, outcome="PASS"),
    ]

    assert per_task_pass_at_k(results, k=2) == {"t1": True}
    assert per_task_pass_hat_k(results, k=2) == {"t1": False}


def test_none_outcome_counts_as_fail() -> None:
    results = [_FakePersistedResult(item_id="t1", attempt_idx=0, outcome=None)]

    assert per_task_pass_at_k(results, k=1) == {"t1": False}
