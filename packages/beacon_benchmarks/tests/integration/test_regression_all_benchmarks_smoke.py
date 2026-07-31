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
    "spider2_lite": "spider2_lite_sample.jsonl",
    "dabstep": "dabstep_sample.jsonl",
    "insightbench": "insightbench_sample.jsonl",
    "drbench": "drbench_sample.jsonl",
    "dsbench_da": "dsbench_da_sample.jsonl",
    "dsbench_dm": "dsbench_dm_sample.jsonl",
    "fdabench": "fdabench_sample.jsonl",
    "text2vis": "text2vis_sample.jsonl",
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


class _MissingJudgeCache:
    def get_or_call(self, _request: Any) -> Any:
        raise RuntimeError("regression rubric graders require a judge cache")


def _primary_grader_for(name: str) -> Any:
    if name in {"bird_minidev", "spider2_lite"}:
        from beacon_graders.graders import ExecutionGroundedSqlGrader

        grader: Any = ExecutionGroundedSqlGrader(engine_factory=_missing_engine_factory)
        if name == "bird_minidev":
            grader.name = "bird.exec_sql.regression"
            grader.suite_filter = "bird_minidev_v2"
        else:
            grader.name = "spider2.exec_sql.regression"
            grader.suite_filter = "spider2_lite_v1"
        grader.order_sensitive_when_order_by = True
        return grader

    if name in {"dabstep", "dsbench_da", "fdabench", "text2vis"}:
        from beacon_graders.graders import DabstepAnswerMatcher

        grader = DabstepAnswerMatcher()
        if name == "dabstep":
            grader.name = "dabstep.factoid.regression"
            grader.suite_filter = "dabstep_v1"
            grader.handle_not_applicable = True
            grader.numeric_rel_tol = 1e-4
            grader.numeric_abs_tol = 1e-4
            grader.string_similarity_threshold = 0.95
        elif name == "dsbench_da":
            grader.name = "dsbench_da.regression"
            grader.suite_filter = "dsbench_da_v1"
            grader.handle_not_applicable = False
            grader.numeric_rel_tol = 1e-4
            grader.numeric_abs_tol = 1e-4
            grader.string_similarity_threshold = 0.95
        elif name == "fdabench":
            grader.name = "fdabench.regression"
            grader.suite_filter = "fdabench_lite_v1"
            grader.handle_not_applicable = False
            grader.numeric_rel_tol = 0.0
            grader.numeric_abs_tol = 0.0
            grader.string_similarity_threshold = 1.0
        else:
            grader.name = "text2vis.regression"
            grader.suite_filter = "text2vis_v1"
            grader.handle_not_applicable = False
            grader.numeric_rel_tol = 1e-4
            grader.numeric_abs_tol = 1e-4
            grader.string_similarity_threshold = 0.95
        return grader

    if name in {"insightbench", "drbench"}:
        from importlib.resources import files

        from beacon_graders.graders import HierarchicalRubricGrader

        judge_cache: Any = _MissingJudgeCache()
        grader = HierarchicalRubricGrader(judge_cache=judge_cache)
        rubric = files(f"beacon_benchmarks.{name}.rubrics").joinpath("default.yaml")
        grader.name = f"{name}.regression"
        grader.rubric_path = str(rubric)
        grader.response_field = "summary" if name == "insightbench" else "report"
        grader.reference_field = grader.response_field
        if name == "insightbench":
            grader.suite_filter = "insightbench_v1"
            grader.scoring_mode = "point"
        else:
            grader.suite_filter = "drbench_v1"
            grader.scoring_mode = "point"
        return grader

    if name == "dsbench_dm":
        from beacon_benchmarks.dsbench_dm.csv_grader import CsvPredictionGrader

        return CsvPredictionGrader()

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
