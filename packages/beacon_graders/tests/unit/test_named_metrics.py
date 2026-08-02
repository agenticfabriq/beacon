"""Two metrics over one run, and no order-dependent outcome (B18).

The tracker reports EX (exact result match) *and* got-facts (right data,
tolerant shape) for the same run. The composer reduced to one outcome by
precedence, and with two execution graders it returned whichever came first in
the constructor — so the same result composed PASS or FAIL depending on
argument order.
"""

from __future__ import annotations

import pytest
from beacon_graders.composer import AmbiguousPrimaryMetricError, VerdictComposer
from beacon_graders.types import GraderKind, Verdict, VerdictOutcome
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep


class _Metric:
    version = "v1"
    kind = GraderKind.EXECUTION

    def __init__(self, *, name: str, metric: str | None, passes: bool) -> None:
        self.name = name
        self.metric = metric
        self._passes = passes

    def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:  # noqa: ARG002
        return True

    def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:  # noqa: ARG002
        return [
            Verdict(
                grader=self.name,
                grader_version=self.version,
                criterion="correctness",
                bool_value=self._passes,
                value=1.0 if self._passes else 0.0,
            )
        ]


def _item() -> EvalItem:
    return EvalItem(
        item_id="i-0", suite="s", query={}, ground_truth={"sql": "SELECT 1"}, metadata={}
    )


def _result() -> ExecutionResult:
    return ExecutionResult(
        output={"sql": "SELECT 1"},
        output_kind="sql",
        trace=ExecutionStep(uuid="r", name="r", level="workflow"),
    )


def _strict() -> _Metric:
    return _Metric(name="bird.exec_sql", metric="ex", passes=False)


def _tolerant() -> _Metric:
    return _Metric(name="bird.exec_sql_tolerant", metric="got_facts", passes=True)


def test_the_primary_metric_decides_the_outcome_regardless_of_order() -> None:
    """The exact defect: same result, both orders, one answer."""
    forward = VerdictComposer(graders=[_strict(), _tolerant()], primary_metric="ex")
    reverse = VerdictComposer(graders=[_tolerant(), _strict()], primary_metric="ex")

    assert forward.compose(_item(), _result())[1] is VerdictOutcome.FAIL
    assert reverse.compose(_item(), _result())[1] is VerdictOutcome.FAIL


def test_choosing_the_tolerant_metric_flips_the_outcome_deliberately() -> None:
    composer = VerdictComposer(graders=[_strict(), _tolerant()], primary_metric="got_facts")

    assert composer.compose(_item(), _result())[1] is VerdictOutcome.PASS


def test_two_execution_graders_without_a_primary_is_an_error() -> None:
    """Silently picking by order is what produced the defect. Refuse instead."""
    with pytest.raises(AmbiguousPrimaryMetricError, match="ex.*got_facts|got_facts.*ex"):
        VerdictComposer(graders=[_strict(), _tolerant()])


def test_a_single_execution_grader_needs_no_primary() -> None:
    """The common case stays as it was."""
    composer = VerdictComposer(graders=[_strict()])

    assert composer.compose(_item(), _result())[1] is VerdictOutcome.FAIL


def test_verdicts_carry_their_metric_name() -> None:
    """Persisted verdicts must be self-describing: adapters rename graders."""
    composer = VerdictComposer(graders=[_strict(), _tolerant()], primary_metric="ex")

    verdicts, _ = composer.compose(_item(), _result())

    assert {v.metric for v in verdicts} == {"ex", "got_facts"}


def test_both_metrics_are_measured_on_the_same_run() -> None:
    """The point of the change: one run, two readings of correctness."""
    composer = VerdictComposer(graders=[_strict(), _tolerant()], primary_metric="ex")

    verdicts, outcome = composer.compose(_item(), _result())

    by_metric = {v.metric: v.bool_value for v in verdicts}
    assert by_metric == {"ex": False, "got_facts": True}
    assert outcome is VerdictOutcome.FAIL


def test_an_unknown_primary_metric_is_rejected() -> None:
    with pytest.raises(AmbiguousPrimaryMetricError, match="no grader"):
        VerdictComposer(graders=[_strict()], primary_metric="nonexistent")


def test_graders_without_a_metric_still_work() -> None:
    """Existing graders declare no metric and must keep composing."""
    plain = _Metric(name="plain", metric=None, passes=True)
    composer = VerdictComposer(graders=[plain])

    verdicts, outcome = composer.compose(_item(), _result())

    assert outcome is VerdictOutcome.PASS
    assert verdicts[0].metric is None
