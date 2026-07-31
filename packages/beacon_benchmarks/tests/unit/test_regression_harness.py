"""Regression harness tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from beacon_benchmarks.regression.harness import BaselinePrediction, replay_and_compare


class StubGrader:
    """Stub grader that passes iff response equals ground truth."""

    name = "stub"
    version = "v1"

    def applicable(self, item: Any, result: Any) -> bool:
        return True

    def grade(self, item: Any, result: Any) -> Any:
        passed = result.response == item.ground_truth
        return SimpleNamespace(
            outcome="PASS" if passed else "FAIL",
            score=1.0 if passed else 0.0,
        )


class FailingGrader:
    """Stub grader that raises during grading."""

    name = "failing"
    version = "v1"

    def applicable(self, item: Any, result: Any) -> bool:  # noqa: ARG002
        return True

    def grade(self, item: Any, result: Any) -> Any:  # noqa: ARG002
        raise RuntimeError("grader unavailable")


def _make_pred(
    task_id: str,
    response: str,
    ground_truth: str,
    baseline_score: float,
) -> BaselinePrediction:
    return BaselinePrediction(
        task_id=task_id,
        item={"task_id": task_id, "ground_truth": ground_truth},
        result={"response": response},
        baseline_score=baseline_score,
    )


def test_replay_matches_when_all_agree() -> None:
    predictions = [
        _make_pred("t1", "a", "a", 1.0),
        _make_pred("t2", "b", "c", 0.0),
    ]

    report = replay_and_compare(predictions, grader=StubGrader(), tolerance=0.01)

    assert report.n_total == 2
    assert report.n_disagree == 0
    assert report.disagree_rate == 0.0
    assert report.within_tolerance is True


def test_replay_reports_baseline_and_beacon_pass_at_1() -> None:
    predictions = [
        _make_pred("t1", "a", "a", 1.0),
        _make_pred("t2", "b", "c", 0.0),
        _make_pred("t3", "c", "c", 1.0),
    ]

    report = replay_and_compare(predictions, grader=StubGrader(), tolerance=0.01)

    assert report.baseline_pass_at_1 == pytest.approx(2 / 3)
    assert report.beacon_pass_at_1 == pytest.approx(2 / 3)
    assert report.pass_at_1_delta_pp == pytest.approx(0.0)
    assert report.pass_at_1_within_tolerance is True


def test_replay_flags_disagreement() -> None:
    predictions = [
        _make_pred("t1", "a", "a", 0.0),
    ]

    report = replay_and_compare(predictions, grader=StubGrader(), tolerance=0.01)

    assert report.n_disagree == 1
    assert report.within_tolerance is False
    assert report.disagreements[0]["task_id"] == "t1"


def test_replay_within_tolerance_when_below_threshold() -> None:
    predictions = [_make_pred(f"a{i}", "x", "x", 1.0) for i in range(200)] + [
        _make_pred("disagree", "y", "x", 1.0)
    ]

    report = replay_and_compare(predictions, grader=StubGrader(), tolerance=0.01)

    assert report.disagree_rate < 0.01
    assert report.within_tolerance is True


def test_replay_breaks_tolerance_when_above_threshold() -> None:
    predictions = [_make_pred(f"a{i}", "x", "x", 1.0) for i in range(50)] + [
        _make_pred(f"d{i}", "y", "x", 1.0) for i in range(10)
    ]

    report = replay_and_compare(predictions, grader=StubGrader(), tolerance=0.01)

    assert report.within_tolerance is False


def test_replay_treats_grader_exception_as_fail() -> None:
    predictions = [_make_pred("t1", "a", "a", 0.0)]

    report = replay_and_compare(predictions, grader=FailingGrader(), tolerance=0.01)

    assert report.n_disagree == 0
    assert report.within_tolerance is True
