"""DABStep regression validation against Beacon's answer matcher."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from beacon_benchmarks.regression.harness import load_fixture, replay_and_compare

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def test_dabstep_regression_within_tolerance(fixtures_dir: Path) -> None:
    fixture = fixtures_dir / "dabstep_sample.jsonl"
    if not fixture.exists() or fixture.stat().st_size == 0:
        pytest.skip("DABStep fixture not yet generated; see fixtures/README.md")

    predictions = load_fixture(fixture)
    assert predictions

    from beacon_graders.graders import DabstepAnswerMatcher

    grader: Any = DabstepAnswerMatcher()
    grader.name = "dabstep.factoid.regression"
    grader.suite_filter = "dabstep_v1"
    grader.handle_not_applicable = True
    grader.numeric_rel_tol = 1e-4
    grader.numeric_abs_tol = 1e-4
    grader.string_similarity_threshold = 0.95
    report = replay_and_compare(
        predictions,
        grader=grader,
        tolerance=0.01,
        benchmark="dabstep",
    )

    assert report.within_tolerance, (
        f"DABStep grader regression failed: {report.n_disagree}/{report.n_total} "
        f"disagreements (rate {report.disagree_rate:.4f})\n"
        f"First 5: {report.disagreements[:5]}"
    )
