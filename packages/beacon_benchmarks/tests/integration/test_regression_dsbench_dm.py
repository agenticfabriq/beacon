"""DSBench-DM regression validation against Beacon's CSV RPG grader."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_benchmarks.regression.harness import load_fixture, replay_and_compare

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def test_dsbench_dm_regression_within_tolerance(fixtures_dir: Path) -> None:
    fixture = fixtures_dir / "dsbench_dm_sample.jsonl"
    if not fixture.exists() or fixture.stat().st_size == 0:
        pytest.skip("DSBench-DM fixture not yet generated")

    predictions = load_fixture(fixture)
    assert predictions

    from beacon_benchmarks.dsbench_dm.csv_grader import CsvPredictionGrader

    report = replay_and_compare(
        predictions,
        grader=CsvPredictionGrader(),
        tolerance=0.01,
        benchmark="dsbench_dm",
    )

    assert report.within_tolerance, report.disagreements[:5]
