"""BIRD regression validation against Beacon's SQL execution grader."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from beacon_benchmarks.regression.harness import load_fixture, replay_and_compare

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def _missing_engine_factory(_item: Any) -> Any:
    raise RuntimeError("BIRD regression fixture requires a configured engine factory")


def test_bird_regression_within_tolerance(fixtures_dir: Path) -> None:
    fixture = fixtures_dir / "bird_minidev_sample.jsonl"
    if not fixture.exists() or fixture.stat().st_size == 0:
        pytest.skip("BIRD fixture not yet generated")

    predictions = load_fixture(fixture)
    assert predictions

    from beacon_graders.graders import ExecutionGroundedSqlGrader

    grader = ExecutionGroundedSqlGrader(engine_factory=_missing_engine_factory)
    report = replay_and_compare(
        predictions,
        grader=grader,
        tolerance=0.01,
        benchmark="bird_minidev",
    )

    assert report.within_tolerance, (
        f"BIRD grader regression failed: {report.n_disagree}/{report.n_total} "
        f"disagreements (rate {report.disagree_rate:.4f} > 0.01)\n"
        f"First 5: {report.disagreements[:5]}"
    )
