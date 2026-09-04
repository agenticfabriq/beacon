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


def test_a_grader_that_emits_two_metrics_can_have_either_decide() -> None:
    """One grader, two readings, and the suite says which one is its headline.

    `ResultSetMatchGrader` emits both readings itself -- strict `exact_match`
    and tolerant `got_facts` -- but declared only the first as its `metric`, so
    `primary_metric="got_facts"` is refused as "no grader declaring it". That
    is why the ingest path cannot yet honour a suite whose headline is the
    tolerant reading, and spider2_lite_local_v1 is exactly that suite (B74):
    its outcomes compose under exact_match while every aggregate and every
    regrade read got_facts. This commit removes the refusal; the caller that
    requests it lands separately, so B74 is still open here.
    A grader that EMITS a metric can have it decide. Declaring one of two
    readings as "the" metric is about which decides BY DEFAULT, not about which
    exist.

    Asserted through compose(), not through the constructor. An earlier version
    of this test only checked `composer.primary_metric == metric`, which
    `_resolve_primary_metric` satisfies by returning its argument verbatim --
    so it established acceptance and never that the chosen reading DECIDES. A
    shortcut like `if len(self.graders) == 1: return True` in
    `_decides_outcome` survived it and the whole suite, and with one grader
    emitting two readings that hands the outcome back to whichever verdict came
    first: B74 verbatim.
    """
    from beacon_graders.graders.result_set_match import ResultSetMatchGrader

    # Right facts, wrong shape: an extra column gold does not carry, so the
    # strict reading fails and the tolerant one holds. The two readings
    # disagree, which is the only situation where the choice is observable.
    gold = {"accepted_results": [{"columns": ["m", "t"], "rows": [["01", 10.0]]}]}
    item = EvalItem(item_id="i", suite="s", query={}, ground_truth=gold, metadata={})
    result = ExecutionResult(
        output={"sql": "SELECT 1", "columns": ["m", "t", "extra"], "rows": [["01", 10.0, "x"]]},
        output_kind="sql",
        trace=ExecutionStep(uuid="0", name="r", level="task"),
    )

    outcomes = {}
    for metric in ("exact_match", "got_facts"):
        composer = VerdictComposer(graders=[ResultSetMatchGrader()], primary_metric=metric)
        assert composer.primary_metric == metric
        _, outcomes[metric] = composer.compose(item, result)

    assert outcomes["exact_match"] is VerdictOutcome.FAIL
    assert outcomes["got_facts"] is VerdictOutcome.PASS


def test_a_metric_no_grader_emits_is_still_refused() -> None:
    """Widening to emitted metrics must not accept anything at all.

    The check exists because picking whichever grader was listed first made the
    same result compose PASS or FAIL by argument order. Accepting an unknown
    name would put that back, silently: nothing would decide the outcome and
    the composer would fall through to its terminal ERROR.
    """
    from beacon_graders.graders.result_set_match import ResultSetMatchGrader

    with pytest.raises(AmbiguousPrimaryMetricError, match="nonesuch"):
        VerdictComposer(graders=[ResultSetMatchGrader()], primary_metric="nonesuch")


def test_emits_does_not_replace_the_declared_default() -> None:
    """`emits` is a union with `metric`, not a substitute for it.

    A grader may reasonably read `emits` as "the ADDITIONAL readings" and
    declare only the second one while keeping `metric` as its default. Under an
    `elif` that dropped the default from the accepted set, and the operator saw
    every push fail with `primary_metric 'exact_match' has no grader declaring
    it` while every verdict in the database carried exactly that name -- a
    message pointing away from its cause.

    ResultSetMatchGrader escapes it only by repeating `exact_match` in both
    places, and nothing enforces that overlap.
    """

    class _AdditionalOnly:
        name = "partial"
        version = "v1"
        kind = GraderKind.EXECUTION
        metric = "exact_match"
        emits = ("got_facts",)

        def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:
            return True

        def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:
            return []

    for metric in ("exact_match", "got_facts"):
        composer = VerdictComposer(graders=[_AdditionalOnly()], primary_metric=metric)

        assert composer.primary_metric == metric
