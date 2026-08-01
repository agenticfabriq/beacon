"""DSBench-DM grades through the composer rather than composing ERROR (B13)."""

from __future__ import annotations

import pytest
from beacon_benchmarks.dsbench_dm.csv_grader import CsvPredictionGrader
from beacon_graders.composer import VerdictComposer
from beacon_graders.types import VerdictOutcome
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep

SUITE = "dsbench_dm_v1"
_TRUTH_CSV = "id,y\n1,10\n2,20\n3,30\n"


def _item() -> EvalItem:
    return EvalItem(
        item_id="dm-0",
        suite=SUITE,
        query={"question": "predict y"},
        ground_truth={
            "test_csv": _TRUTH_CSV,
            "target_col": "y",
            "metric": "mae",
            "baseline_score": 10.0,
            "best_score": 0.0,
        },
        metadata={},
    )


def _result(predictions_csv: str | None) -> ExecutionResult:
    output: dict[str, object] = {}
    if predictions_csv is not None:
        output["predictions_csv"] = predictions_csv
    return ExecutionResult(
        output=output,
        output_kind="json",
        trace=ExecutionStep(uuid="r", name="r", level="workflow"),
    )


def _compose(predictions_csv: str | None) -> VerdictOutcome:
    composer = VerdictComposer(graders=[CsvPredictionGrader(suite_filter=SUITE)])
    _, outcome = composer.compose(_item(), _result(predictions_csv))
    return outcome


def test_a_perfect_prediction_composes_pass() -> None:
    """Previously every DSBench-DM item composed ERROR, however good the answer."""
    assert _compose(_TRUTH_CSV) is VerdictOutcome.PASS


def test_a_poor_prediction_composes_fail_not_error() -> None:
    """A wrong answer must be distinguishable from a broken grader."""
    assert _compose("id,y\n1,1000\n2,2000\n3,3000\n") is VerdictOutcome.FAIL


def test_a_missing_prediction_composes_fail() -> None:
    assert _compose(None) is VerdictOutcome.FAIL


def test_the_verdict_carries_the_rpg_score() -> None:
    grader = CsvPredictionGrader(suite_filter=SUITE)
    verdicts = grader.grade(_item(), _result(_TRUTH_CSV))

    assert len(verdicts) == 1
    verdict = verdicts[0]
    assert verdict.grader == "dsbench_dm.csv.rpg"
    assert verdict.criterion == "relative_performance_gap"
    assert verdict.bool_value is True
    assert verdict.value == pytest.approx(1.0)


def test_grade_returns_a_list_so_compose_can_extend_it() -> None:
    """compose does verdicts.extend(...); a bare object raised there."""
    grader = CsvPredictionGrader(suite_filter=SUITE)
    emitted = grader.grade(_item(), _result(_TRUTH_CSV))

    collected: list[object] = []
    collected.extend(emitted)
    assert len(collected) == 1
