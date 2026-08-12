"""Cross-cutting regression smoke for all benchmark adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from beacon_benchmarks.regression.harness import load_fixture, replay_and_compare

pytestmark = pytest.mark.integration

FIXTURES = {
    "bird_minidev": "bird_minidev_sample.jsonl",
}


def _expected_path(name: str) -> Path:
    return Path("tests") / "regression" / name / "expected.json"


def _load_expected(name: str) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(_expected_path(name).read_text(encoding="utf-8")))


def test_expected_baselines_exist_for_all_adapters() -> None:
    missing = [name for name in FIXTURES if not _expected_path(name).exists()]
    assert missing == []


def _missing_engine_factory(_item: Any) -> Any:
    raise RuntimeError("regression SQL graders require an engine factory")


def _primary_grader_for(name: str) -> Any:
    if name == "bird_minidev":
        from beacon_graders.graders import ExecutionGroundedSqlGrader

        grader: Any = ExecutionGroundedSqlGrader(engine_factory=_missing_engine_factory)
        grader.name = "bird.exec_sql.regression"
        grader.suite_filter = "bird_minidev_v2"
        grader.order_sensitive_when_order_by = True
        return grader

    raise AssertionError(f"unknown adapter for regression: {name}")


@pytest.mark.parametrize("name,filename", sorted(FIXTURES.items()))
def test_regression_within_tolerance(name: str, filename: str, fixtures_dir: Any) -> None:
    fixture = fixtures_dir / filename
    if not fixture.exists() or fixture.stat().st_size == 0:
        pytest.skip(f"{name} fixture not yet generated")

    predictions = load_fixture(fixture)
    if not predictions:
        pytest.skip(f"{name} fixture is empty")

    expected = _load_expected(name)
    assert len(predictions) >= int(expected["minimum_tasks"])
    assert all(
        prediction.item.get("metadata", {}).get("source") != "built_in_regression_fixture"
        for prediction in predictions
    )

    grader = _primary_grader_for(name)
    report = replay_and_compare(
        predictions,
        grader=grader,
        tolerance=0.01,
        benchmark=name,
    )

    assert report.baseline_pass_at_1 == pytest.approx(expected["expected_pass_at_1"])
    assert report.pass_at_1_within_tolerance
    assert report.within_tolerance, (
        f"{name} regression failed: {report.n_disagree}/{report.n_total} "
        f"disagreements (rate {report.disagree_rate:.4f} > 0.01)\n"
        f"First 5: {report.disagreements[:5]}"
    )
