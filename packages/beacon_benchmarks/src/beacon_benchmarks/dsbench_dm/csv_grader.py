"""Relative Performance Gap (RPG) grader for DSBench-DM CSV predictions."""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from typing import Any

_LOWER_IS_BETTER = {"mae", "rmse"}


@dataclass(frozen=True)
class _Verdict:
    outcome: str
    score: float
    justification: str


def _read_csv_dict(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def score_predictions(
    pred_csv: str,
    truth_csv: str,
    *,
    target_col: str,
    metric: str,
) -> float:
    """Score CSV predictions against truth rows using the configured metric."""
    pred_rows = _read_csv_dict(pred_csv)
    truth_rows = _read_csv_dict(truth_csv)
    if len(pred_rows) != len(truth_rows):
        raise ValueError(f"prediction row count {len(pred_rows)} != truth {len(truth_rows)}")
    if not pred_rows:
        raise ValueError("prediction CSV has no rows")

    if metric in _LOWER_IS_BETTER:
        pred_vals = [float(row[target_col]) for row in pred_rows]
        truth_vals = [float(row[target_col]) for row in truth_rows]
        if metric == "rmse":
            squared_error = sum(
                (pred - truth) ** 2 for pred, truth in zip(pred_vals, truth_vals, strict=True)
            )
            return math.sqrt(squared_error / len(pred_vals))
        return sum(
            abs(pred - truth) for pred, truth in zip(pred_vals, truth_vals, strict=True)
        ) / len(pred_vals)

    if metric == "accuracy":
        correct = sum(
            1
            for pred, truth in zip(pred_rows, truth_rows, strict=True)
            if pred[target_col] == truth[target_col]
        )
        return correct / len(pred_rows)

    raise ValueError(f"unsupported metric: {metric}")


def compute_rpg(
    *,
    agent_score: float,
    baseline_score: float,
    best_score: float,
    metric: str,
) -> float:
    """Return a clipped Relative Performance Gap where higher is better."""
    if metric in _LOWER_IS_BETTER:
        denominator = baseline_score - best_score
        if denominator == 0:
            return 1.0 if agent_score == best_score else 0.0
        rpg = (baseline_score - agent_score) / denominator
    else:
        denominator = best_score - baseline_score
        if denominator == 0:
            return 1.0 if agent_score == best_score else 0.0
        rpg = (agent_score - baseline_score) / denominator
    return max(0.0, min(1.0, rpg))


def _payload_attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


class CsvPredictionGrader:
    """Score DSBench-DM prediction CSVs with Relative Performance Gap."""

    name = "dsbench_dm.csv.rpg"
    version = "1.0"

    def __init__(self, *, suite_filter: str = "dsbench_dm_v1") -> None:
        self.suite_filter = suite_filter

    def applicable(self, item: Any, result: Any) -> bool:  # noqa: ARG002
        """Apply when ``item.suite`` matches this grader's DSBench-DM suite filter."""
        suite = _payload_attr(item, "suite")
        return isinstance(suite, str) and suite == self.suite_filter

    def grade(self, item: Any, result: Any) -> _Verdict:
        """Score the agent's ``predictions_csv`` via RPG against baseline/best."""
        ground_truth = _payload_attr(item, "ground_truth") or {}
        truth_csv = ground_truth["test_csv"]
        target_col = ground_truth["target_col"]
        metric = ground_truth["metric"]
        baseline_score = float(ground_truth["baseline_score"])
        best_score = float(ground_truth["best_score"])

        response = _payload_attr(result, "response") or _payload_attr(result, "output") or {}
        pred_csv = response.get("predictions_csv") if isinstance(response, dict) else None
        if not pred_csv:
            return _Verdict(
                outcome="FAIL",
                score=0.0,
                justification="agent produced no predictions_csv",
            )

        try:
            agent_score = score_predictions(
                pred_csv,
                truth_csv,
                target_col=target_col,
                metric=metric,
            )
        except Exception as exc:  # noqa: BLE001
            return _Verdict(
                outcome="FAIL",
                score=0.0,
                justification=f"scoring failed: {exc}",
            )

        rpg = compute_rpg(
            agent_score=agent_score,
            baseline_score=baseline_score,
            best_score=best_score,
            metric=metric,
        )
        outcome = "PASS" if rpg >= 0.5 else "FAIL"
        return _Verdict(
            outcome=outcome,
            score=rpg,
            justification=(
                f"agent_score={agent_score:.4f}, baseline={baseline_score:.4f}, "
                f"best={best_score:.4f}, metric={metric}, RPG={rpg:.4f}"
            ),
        )
