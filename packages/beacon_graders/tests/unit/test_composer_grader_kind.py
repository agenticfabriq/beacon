"""Composition precedence keys on declared kind, not on the mutable name (B5)."""

from __future__ import annotations

from typing import Any

import pytest
from beacon_graders.composer import VerdictComposer
from beacon_graders.graders import (
    DabstepAnswerMatcher,
    ExecutionGroundedSqlGrader,
    FreeTextReferenceGrader,
    HierarchicalRubricGrader,
    Text2VisDataGroundedGrader,
)
from beacon_graders.types import GraderKind, Verdict, VerdictOutcome
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep

_BUILT_IN_GRADER_TYPES = (
    (ExecutionGroundedSqlGrader, GraderKind.EXECUTION),
    (DabstepAnswerMatcher, GraderKind.EXECUTION),
    (Text2VisDataGroundedGrader, GraderKind.EXECUTION),
    (HierarchicalRubricGrader, GraderKind.LLM_JUDGE),
    (FreeTextReferenceGrader, GraderKind.LLM_JUDGE),
)


class _StubGrader:
    """Emits a fixed verdict; stands in for an adapter-registered grader."""

    version = "v1"

    def __init__(self, *, name: str, kind: GraderKind | None, bool_value: bool) -> None:
        self.name = name
        self._bool_value = bool_value
        if kind is not None:
            self.kind = kind

    def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:  # noqa: ARG002
        return True

    def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:  # noqa: ARG002
        return [
            Verdict(
                grader=self.name,
                grader_version=self.version,
                criterion="exec_match",
                bool_value=self._bool_value,
                value=1.0 if self._bool_value else 0.0,
            )
        ]


def _item() -> EvalItem:
    return EvalItem(
        item_id="i-0",
        suite="s",
        query={"question": "q"},
        ground_truth={"answer": "yes"},
        metadata={},
    )


def _result() -> ExecutionResult:
    return ExecutionResult(
        output={"sql": "SELECT 1"},
        output_kind="sql",
        trace=ExecutionStep(uuid="r", name="r", level="workflow"),
    )


@pytest.mark.parametrize(("grader_type", "expected"), _BUILT_IN_GRADER_TYPES)
def test_built_in_graders_declare_their_kind(
    grader_type: type[Any],
    expected: GraderKind,
) -> None:
    assert grader_type.kind is expected


@pytest.mark.parametrize(
    "renamed",
    [
        "bird_minidev_v2.exec_sql",
        "spider2_lite.exec_sql",
        "text2vis.data_grounded",
        "dabstep.factoid",
        "dsbench_da.factoid",
    ],
)
def test_a_renamed_execution_grader_still_decides_the_outcome(renamed: str) -> None:
    """Adapters rename their instances; the verdict must still compose PASS/FAIL."""
    grader = _StubGrader(name=renamed, kind=GraderKind.EXECUTION, bool_value=True)
    composer = VerdictComposer(graders=[grader])

    verdicts, outcome = composer.compose(_item(), _result())

    assert [verdict.grader for verdict in verdicts] == [renamed]
    # Before the fix this fell through every branch and composed ERROR.
    assert outcome is VerdictOutcome.PASS


def test_a_renamed_execution_grader_can_also_compose_fail() -> None:
    grader = _StubGrader(
        name="bird_minidev_v2.exec_sql", kind=GraderKind.EXECUTION, bool_value=False
    )
    composer = VerdictComposer(graders=[grader])

    _, outcome = composer.compose(_item(), _result())

    assert outcome is VerdictOutcome.FAIL


def test_a_grader_without_a_kind_falls_back_to_the_legacy_name_sets() -> None:
    """Third-party graders that predate ``kind`` keep working."""
    grader = _StubGrader(name="execution_grounded_sql", kind=None, bool_value=True)
    composer = VerdictComposer(graders=[grader])

    _, outcome = composer.compose(_item(), _result())

    assert outcome is VerdictOutcome.PASS


def test_an_unclassifiable_grader_still_composes_error() -> None:
    """An unknown, kind-less grader is not silently promoted to a decision."""
    grader = _StubGrader(name="mystery.grader", kind=None, bool_value=True)
    composer = VerdictComposer(graders=[grader])

    _, outcome = composer.compose(_item(), _result())

    assert outcome is VerdictOutcome.ERROR


def test_renaming_after_construction_is_still_honoured() -> None:
    """Adapters mutate .name on an already-built instance."""
    grader = _StubGrader(
        name="execution_grounded_sql",
        kind=GraderKind.EXECUTION,
        bool_value=True,
    )
    composer = VerdictComposer(graders=[grader])
    grader.name = "bird_minidev_v2.exec_sql"

    _, outcome = composer.compose(_item(), _result())

    assert outcome is VerdictOutcome.PASS
