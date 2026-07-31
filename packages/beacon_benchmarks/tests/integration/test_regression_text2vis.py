"""Text2Vis regression validation against Beacon's answer matcher."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from beacon_benchmarks.regression.harness import load_fixture, replay_and_compare

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def test_text2vis_regression_within_tolerance(fixtures_dir: Path) -> None:
    fixture = fixtures_dir / "text2vis_sample.jsonl"
    if not fixture.exists() or fixture.stat().st_size == 0:
        pytest.skip("Text2Vis fixture not yet generated")

    predictions = load_fixture(fixture)
    assert predictions

    from beacon_graders.graders import DabstepAnswerMatcher

    grader: Any = DabstepAnswerMatcher()
    grader.name = "text2vis.factoid.answer.regression"
    grader.suite_filter = "text2vis_v1"
    grader.handle_not_applicable = False
    grader.numeric_rel_tol = 1e-4
    grader.numeric_abs_tol = 1e-4
    grader.string_similarity_threshold = 0.95
    report = replay_and_compare(
        predictions,
        grader=grader,
        tolerance=0.01,
        benchmark="text2vis",
    )

    assert report.within_tolerance, report.disagreements[:5]
