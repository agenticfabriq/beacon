"""Replay baseline predictions through Beacon graders."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class BaselinePrediction:
    """One task prediction and its expected baseline score."""

    task_id: str
    item: dict[str, Any]
    result: dict[str, Any]
    baseline_score: float


@dataclass
class RegressionReport:
    """Summary of replay disagreement against baseline outcomes."""

    benchmark: str
    n_total: int
    n_disagree: int
    disagree_rate: float
    within_tolerance: bool
    baseline_pass_at_1: float
    beacon_pass_at_1: float
    pass_at_1_delta_pp: float
    pass_at_1_within_tolerance: bool
    disagreements: list[dict[str, Any]] = field(default_factory=list)


class _GraderLike(Protocol):
    name: str
    version: str

    def applicable(self, item: Any, result: Any) -> bool:
        """Return whether the grader can score ``item``/``result``."""
        ...

    def grade(self, item: Any, result: Any) -> Any:
        """Score ``item``/``result`` and return a verdict-like value."""
        ...


def load_fixture(path: Path) -> list[BaselinePrediction]:
    """Load baseline predictions from JSONL."""
    predictions: list[BaselinePrediction] = []
    with path.open(encoding="utf-8") as file:
        for line in file:
            stripped = line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            predictions.append(
                BaselinePrediction(
                    task_id=str(row["task_id"]),
                    item=cast("dict[str, Any]", row["item"]),
                    result=cast("dict[str, Any]", row["result"]),
                    baseline_score=float(row["baseline_score"]),
                )
            )
    return predictions


def _to_obj(payload: dict[str, Any]) -> Any:
    return SimpleNamespace(**payload)


def _score_to_outcome(score: float) -> str:
    return "PASS" if score >= 0.5 else "FAIL"


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _first_verdict(raw: Any) -> Any:
    if isinstance(raw, list | tuple):
        return raw[0] if raw else SimpleNamespace(score=0.0)
    return raw


def _verdict_score(verdict: Any) -> float:
    score = _attr(verdict, "score")
    if score is None:
        score = _attr(verdict, "value")
    if score is not None:
        return float(score)

    bool_value = _attr(verdict, "bool_value")
    if bool_value is not None:
        return 1.0 if bool_value else 0.0

    return 0.0


def _verdict_outcome(verdict: Any) -> str:
    outcome = _attr(verdict, "outcome")
    if outcome is not None:
        outcome_value = str(getattr(outcome, "value", outcome)).upper()
        if outcome_value in {"PASS", "FAIL"}:
            return outcome_value

    bool_value = _attr(verdict, "bool_value")
    if bool_value is not None:
        return "PASS" if bool_value else "FAIL"

    return _score_to_outcome(_verdict_score(verdict))


def replay_and_compare(
    predictions: list[BaselinePrediction],
    *,
    grader: _GraderLike,
    tolerance: float = 0.01,
    benchmark: str = "unknown",
) -> RegressionReport:
    """Replay predictions through a grader and compare PASS/FAIL outcomes."""
    disagreements: list[dict[str, Any]] = []
    baseline_passes = 0
    beacon_passes = 0

    for prediction in predictions:
        item = _to_obj(prediction.item)
        result = _to_obj(prediction.result)
        if not grader.applicable(item, result):
            continue

        try:
            verdict = _first_verdict(grader.grade(item, result))
        except Exception as exc:  # noqa: BLE001
            verdict = SimpleNamespace(
                outcome="FAIL",
                score=0.0,
                justification=f"grader raised: {exc!r}",
            )
        beacon_outcome = _verdict_outcome(verdict)
        baseline_outcome = _score_to_outcome(prediction.baseline_score)
        if baseline_outcome == "PASS":
            baseline_passes += 1
        if beacon_outcome == "PASS":
            beacon_passes += 1
        if beacon_outcome != baseline_outcome:
            disagreements.append(
                {
                    "task_id": prediction.task_id,
                    "baseline_outcome": baseline_outcome,
                    "beacon_outcome": beacon_outcome,
                    "beacon_score": _verdict_score(verdict),
                    "baseline_score": prediction.baseline_score,
                }
            )

    n_total = len(predictions)
    n_disagree = len(disagreements)
    disagree_rate = (n_disagree / n_total) if n_total else 0.0
    baseline_pass_at_1 = (baseline_passes / n_total) if n_total else 0.0
    beacon_pass_at_1 = (beacon_passes / n_total) if n_total else 0.0
    pass_at_1_delta_pp = abs(beacon_pass_at_1 - baseline_pass_at_1) * 100.0

    return RegressionReport(
        benchmark=benchmark,
        n_total=n_total,
        n_disagree=n_disagree,
        disagree_rate=disagree_rate,
        within_tolerance=disagree_rate <= tolerance,
        baseline_pass_at_1=baseline_pass_at_1,
        beacon_pass_at_1=beacon_pass_at_1,
        pass_at_1_delta_pp=pass_at_1_delta_pp,
        pass_at_1_within_tolerance=pass_at_1_delta_pp <= tolerance * 100.0,
        disagreements=disagreements,
    )
