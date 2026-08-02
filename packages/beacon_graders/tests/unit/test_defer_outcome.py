"""Declining to answer is its own outcome, not a failure (B16).

A model that declines and a model that answers wrongly scored identically, so
over-deferral — the only lever that moved the mini-dev benchmark from 16% to
49% — was invisible. Measured on two live sweeps: 18 of 45 graded results
carried ``deferred`` and every one was recorded FAIL.
"""

from __future__ import annotations

import pytest
from beacon_graders.composer import VerdictComposer
from beacon_graders.types import GraderKind, Verdict, VerdictOutcome
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep


class _Grader:
    name = "exec"
    version = "v1"
    kind = GraderKind.EXECUTION

    def __init__(self, *, bool_value: bool | None) -> None:
        self._bool_value = bool_value

    def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:  # noqa: ARG002
        return True

    def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:  # noqa: ARG002
        return [
            Verdict(
                grader=self.name,
                grader_version=self.version,
                criterion="correctness",
                bool_value=self._bool_value,
                value=1.0 if self._bool_value else 0.0,
                justification="No SQL produced by SUT." if self._bool_value is False else "match",
            )
        ]


def _item() -> EvalItem:
    return EvalItem(
        item_id="i-0",
        suite="s",
        query={"question": "q"},
        ground_truth={"sql": "SELECT 1"},
        metadata={},
    )


def _result(*, deferred: bool = False, error: str | None = None) -> ExecutionResult:
    return ExecutionResult(
        output={"sql": None if deferred else "SELECT 1", "deferred": deferred},
        output_kind="sql",
        deferred=deferred,
        error=error,
        trace=ExecutionStep(uuid="r", name="r", level="workflow"),
    )


def test_a_declined_answer_composes_defer() -> None:
    composer = VerdictComposer(graders=[_Grader(bool_value=False)])

    _, outcome = composer.compose(_item(), _result(deferred=True))

    assert outcome is VerdictOutcome.DEFER


def test_a_wrong_answer_still_composes_fail() -> None:
    """The distinction only means something if FAIL still means wrong."""
    composer = VerdictComposer(graders=[_Grader(bool_value=False)])

    _, outcome = composer.compose(_item(), _result(deferred=False))

    assert outcome is VerdictOutcome.FAIL


def test_a_correct_answer_still_composes_pass() -> None:
    composer = VerdictComposer(graders=[_Grader(bool_value=True)])

    _, outcome = composer.compose(_item(), _result(deferred=False))

    assert outcome is VerdictOutcome.PASS


def test_infra_error_outranks_deferral() -> None:
    """An attempt that never ran cannot be said to have declined."""
    composer = VerdictComposer(graders=[_Grader(bool_value=False)])

    _, outcome = composer.compose(_item(), _result(deferred=True, error="boom"))

    assert outcome is VerdictOutcome.ERROR


def test_verdicts_are_still_recorded_for_a_deferral() -> None:
    """The grader's evidence survives even though the outcome overrides it."""
    composer = VerdictComposer(graders=[_Grader(bool_value=False)])

    verdicts, outcome = composer.compose(_item(), _result(deferred=True))

    assert outcome is VerdictOutcome.DEFER
    assert [v.grader for v in verdicts] == ["exec"]


def test_the_legacy_output_flag_is_still_honoured() -> None:
    """SUTs that only set output["deferred"] keep working."""
    composer = VerdictComposer(graders=[_Grader(bool_value=False)])
    result = ExecutionResult(
        output={"deferred": True},
        output_kind="sql",
        trace=ExecutionStep(uuid="r", name="r", level="workflow"),
    )

    _, outcome = composer.compose(_item(), result)

    assert outcome is VerdictOutcome.DEFER


@pytest.mark.parametrize("outcome", list(VerdictOutcome))
def test_every_grader_outcome_maps_to_a_storage_outcome(outcome: VerdictOutcome) -> None:
    """The two enums must not drift; persist_result casts across them by value."""
    from beacon_storage.models.runs import VerdictOutcome as StorageOutcome

    assert StorageOutcome(outcome.value) is not None
