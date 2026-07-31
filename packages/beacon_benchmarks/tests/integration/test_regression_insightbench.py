"""InsightBench regression validation against Beacon's rubric grader."""

from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING, Any

import pytest
from beacon_benchmarks.regression.harness import load_fixture, replay_and_compare

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


class _MissingJudgeCache:
    def get_or_call(self, _request: Any) -> Any:
        raise RuntimeError("InsightBench regression requires a configured judge cache")


def test_insightbench_regression_within_tolerance(fixtures_dir: Path) -> None:
    fixture = fixtures_dir / "insightbench_sample.jsonl"
    if not fixture.exists() or fixture.stat().st_size == 0:
        pytest.skip("InsightBench fixture not yet generated")

    predictions = load_fixture(fixture)
    assert predictions

    from beacon_graders.graders import HierarchicalRubricGrader

    rubric = str(files("beacon_benchmarks.insightbench.rubrics").joinpath("default.yaml"))
    judge_cache: Any = _MissingJudgeCache()
    grader: Any = HierarchicalRubricGrader(judge_cache=judge_cache)
    grader.name = "insightbench.rubric.summary.regression"
    grader.suite_filter = "insightbench_v1"
    grader.rubric_path = rubric
    grader.scoring_mode = "point"
    grader.response_field = "summary"
    grader.reference_field = "summary"
    report = replay_and_compare(
        predictions,
        grader=grader,
        tolerance=0.01,
        benchmark="insightbench",
    )

    assert report.within_tolerance, report.disagreements[:5]
