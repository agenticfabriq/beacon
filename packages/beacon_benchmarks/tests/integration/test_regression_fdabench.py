"""FDABench regression validation against Beacon's choice matcher."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from beacon_benchmarks.regression.harness import load_fixture, replay_and_compare

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def test_fdabench_regression_within_tolerance(fixtures_dir: Path) -> None:
    fixture = fixtures_dir / "fdabench_sample.jsonl"
    if not fixture.exists() or fixture.stat().st_size == 0:
        pytest.skip("FDABench fixture not yet generated")

    predictions = load_fixture(fixture)
    assert predictions

    from beacon_graders.graders import DabstepAnswerMatcher

    grader: Any = DabstepAnswerMatcher()
    grader.name = "fdabench.factoid.choice.regression"
    grader.suite_filter = "fdabench_lite_v1"
    grader.handle_not_applicable = False
    grader.numeric_rel_tol = 0.0
    grader.numeric_abs_tol = 0.0
    grader.string_similarity_threshold = 1.0
    report = replay_and_compare(
        predictions,
        grader=grader,
        tolerance=0.01,
        benchmark="fdabench",
    )

    assert report.within_tolerance, report.disagreements[:5]
