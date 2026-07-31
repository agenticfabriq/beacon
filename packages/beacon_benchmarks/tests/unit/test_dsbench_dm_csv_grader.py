"""CsvPredictionGrader computes Relative Performance Gap against leaderboard."""

from __future__ import annotations

import csv
import io
from types import SimpleNamespace
from typing import Any

from beacon_benchmarks.dsbench_dm.csv_grader import (
    CsvPredictionGrader,
    compute_rpg,
    score_predictions,
)


def _csv(rows: list[list[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def test_score_predictions_perfect_score() -> None:
    pred = _csv([["id", "y"], [1, 0.5], [2, 1.0]])
    truth = _csv([["id", "y"], [1, 0.5], [2, 1.0]])

    rmse = score_predictions(pred, truth, target_col="y", metric="rmse")

    assert rmse == 0.0


def test_score_predictions_rmse_basic() -> None:
    pred = _csv([["id", "y"], [1, 0.0], [2, 0.0]])
    truth = _csv([["id", "y"], [1, 1.0], [2, 1.0]])

    rmse = score_predictions(pred, truth, target_col="y", metric="rmse")

    assert rmse == 1.0


def test_compute_rpg_perfect_when_agent_matches_best() -> None:
    rpg = compute_rpg(agent_score=0.0, baseline_score=0.5, best_score=0.0, metric="rmse")

    assert rpg == 1.0


def test_compute_rpg_zero_when_agent_matches_baseline() -> None:
    rpg = compute_rpg(agent_score=0.5, baseline_score=0.5, best_score=0.0, metric="rmse")

    assert rpg == 0.0


def test_grader_applicable_only_for_suite() -> None:
    grader = CsvPredictionGrader()
    item = SimpleNamespace(suite="dsbench_dm_v1")
    result = SimpleNamespace()

    assert grader.applicable(item, result) is True
    assert grader.applicable(SimpleNamespace(suite="dsbench_da_v1"), result) is False


def test_grader_grade_returns_score() -> None:
    grader = CsvPredictionGrader()
    item = SimpleNamespace(
        suite="dsbench_dm_v1",
        ground_truth={
            "test_csv": _csv([["id", "y"], [1, 1.0], [2, 0.5]]),
            "target_col": "y",
            "metric": "rmse",
            "baseline_score": 1.0,
            "best_score": 0.0,
        },
    )
    result = SimpleNamespace(
        response={"predictions_csv": _csv([["id", "y"], [1, 1.0], [2, 0.5]])},
    )

    verdict = grader.grade(item, result)

    assert verdict.score == 1.0
    assert verdict.outcome == "PASS"
