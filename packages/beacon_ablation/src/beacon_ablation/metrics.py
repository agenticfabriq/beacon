"""Aggregate runner result rows into per-task outcomes and suite rates."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, TypeVar

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Iterable

_MISSING = object()
_ResultT = TypeVar("_ResultT")


def _get_attr(result: object, primary: str, fallback: str | None = None) -> object:
    value: object = getattr(result, primary, _MISSING)
    if value is _MISSING and fallback is not None:
        value = getattr(result, fallback, _MISSING)
    if value is _MISSING:
        names = primary if fallback is None else f"{primary!r} or {fallback!r}"
        raise AttributeError(f"result row must expose {names}")
    return value


def _task_id(result: object) -> str:
    return str(_get_attr(result, "task_id", "item_id"))


def _attempt_idx(result: object) -> int:
    value = _get_attr(result, "attempt_idx")
    if not isinstance(value, int):
        raise TypeError(f"attempt_idx must be int, got {type(value).__name__}")
    return value


def _is_pass(result: object) -> bool:
    value = _get_attr(result, "verdict", "outcome")
    return value is not None and str(value) == "PASS"


def _int_attr(result: object, name: str) -> int:
    value = _get_attr(result, name)
    if not isinstance(value, int):
        raise TypeError(f"{name} must be int, got {type(value).__name__}")
    return value


def gradeable_results(results: Iterable[_ResultT]) -> list[_ResultT]:
    """Return only the attempts that actually produced a grade.

    An ``ERROR`` outcome means the harness or the endpoint failed, not that the
    solution answered wrongly, so counting it as a failure lets an outage read
    as a quality regression. Dropping those attempts also drops any item whose
    every attempt errored -- such an item leaves the denominator entirely
    rather than scoring zero. Report the number of excluded items alongside the
    rate, or a mostly-broken run looks healthy.

    ``TIMEOUT`` is deliberately kept: it is an outcome of the attempt itself.
    """
    return [result for result in results if str(_get_attr(result, "verdict", "outcome")) != "ERROR"]


def restrict_to_k_attempts(results: Iterable[_ResultT], *, k: int) -> list[_ResultT]:
    """Drop tasks with fewer than ``k`` attempts so ``pass@k`` means what it says.

    ``per_task_pass_at_k`` slices ``attempts[:k]``, which silently degrades to
    ``pass@(available)`` for a short task. Dropping the task instead also keeps
    the paired statistics honest: ``bootstrap_paired_ci`` and ``mcnemar_exact``
    both compare on the intersection of task ids, so a task that falls out of
    one arm leaves the comparison rather than being scored against an arm that
    never managed to grade it.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    return [
        result
        for attempts in _group_by_task(results).values()
        if len(attempts) >= k
        for result in attempts
    ]


def min_attempts_per_task(results: Iterable[object]) -> int:
    """Return the fewest attempts any single task has, or 0 when there are none.

    ``pass@k`` is only answerable for a suite once every task has at least ``k``
    attempts; below that the helpers silently degrade to ``pass@(available)``.
    """
    by_task = _group_by_task(results)
    if not by_task:
        return 0
    return min(len(attempts) for attempts in by_task.values())


def _group_by_task(results: Iterable[_ResultT]) -> dict[str, list[_ResultT]]:
    by_task: dict[str, list[_ResultT]] = defaultdict(list)
    for result in results:
        by_task[_task_id(result)].append(result)
    for attempts in by_task.values():
        attempts.sort(key=_attempt_idx)
    return dict(by_task)


def per_task_pass_at_k(results: Iterable[object], *, k: int) -> dict[str, bool]:
    """Return whether each task has any PASS verdict among the first k attempts."""
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    return {
        task_id: any(_is_pass(result) for result in attempts[:k])
        for task_id, attempts in _group_by_task(results).items()
    }


def per_task_pass_hat_k(results: Iterable[object], *, k: int) -> dict[str, bool]:
    """Return whether each task has k available attempts and all first k pass."""
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")

    out: dict[str, bool] = {}
    for task_id, attempts in _group_by_task(results).items():
        out[task_id] = len(attempts) >= k and all(_is_pass(result) for result in attempts[:k])
    return out


def suite_pass_at_k(results: Iterable[object], *, k: int) -> float | None:
    """Return suite pass@k as the mean of per-task pass@k booleans.

    ``None`` when there are no tasks to average. A mean over an empty set is
    undefined, and returning 0.0 made "nothing was gradeable" indistinguishable
    from "everything failed" -- which is how an arm wiped out by an endpoint
    outage came to be recorded as having scored zero.
    """
    per_task = per_task_pass_at_k(results, k=k)
    if not per_task:
        return None
    return sum(per_task.values()) / len(per_task)


def suite_pass_hat_k(results: Iterable[object], *, k: int) -> float | None:
    """Return suite pass^k as the mean of per-task pass^k booleans.

    ``None`` when there are no tasks to average -- see :func:`suite_pass_at_k`.
    """
    per_task = per_task_pass_hat_k(results, k=k)
    if not per_task:
        return None
    return sum(per_task.values()) / len(per_task)


def median_total_tokens(results: Iterable[object]) -> float | None:
    """Return median input-plus-output tokens over the results that recorded them.

    ``None`` when none did. Token cost is nullable because a runner whose
    report format carries no per-item count has nothing to report, and a result
    that recorded no cost is not a result that cost nothing -- averaging those
    in as zeros drags the median toward a configuration nobody measured.

    A total needs BOTH halves. The in-process SUT reports one number and it is
    the output half; calling that the total assumes a free prompt, and for a
    schema-carrying SQL prompt the input half is usually the larger one.
    """
    totals = [
        result_input + result_output
        for result in results
        if isinstance(result_input := _get_attr(result, "tokens_input"), int)
        and isinstance(result_output := _get_attr(result, "tokens_output"), int)
    ]
    if not totals:
        return None
    return float(np.median(totals))


def median_runtime_ms(results: Iterable[object]) -> float | None:
    """Return median runtime in milliseconds, or None for empty input."""
    runtimes = [_int_attr(result, "runtime_ms") for result in results]
    if not runtimes:
        return None
    return float(np.median(runtimes))
