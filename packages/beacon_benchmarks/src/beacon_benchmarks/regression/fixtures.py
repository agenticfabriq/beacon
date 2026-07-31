"""Built-in benchmark regression fixture generation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

FixtureRow = dict[str, Any]
_REFERENCE_SUT = "beacon-offline-reference-v1"
_SOURCE = "hand_curated_regression_set"


def fixture_rows_for(name: str, *, max_tasks: int = 100) -> list[FixtureRow]:
    """Return representative baseline fixture rows for a benchmark adapter."""
    rows = _fixture_rows().get(name)
    if rows is None:
        raise ValueError(f"unsupported regression fixture: {name}")
    return rows[:max_tasks]


def write_fixture(path: Path, rows: list[FixtureRow]) -> None:
    """Write regression fixture rows as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, sort_keys=True))
            file.write("\n")


def default_fixture_filename(name: str) -> str:
    """Return the conventional fixture filename for an adapter name."""
    return f"{name}_sample.jsonl"


def _fixture_rows() -> dict[str, list[FixtureRow]]:
    return {
        "bird_minidev": _sql_rows(prefix="bird", suite="bird_minidev_v2"),
        "spider2_lite": _sql_rows(prefix="spider2", suite="spider2_lite_v1"),
        "dabstep": _factoid_rows(prefix="dabstep", suite="dabstep_v1"),
        "drbench": _rubric_rows(prefix="drbench", suite="drbench_v1"),
        "dsbench_da": _factoid_rows(prefix="dsbench-da", suite="dsbench_da_v1"),
        "dsbench_dm": _dsbench_dm_rows(),
        "fdabench": _factoid_rows(prefix="fdabench", suite="fdabench_lite_v1"),
        "insightbench": _rubric_rows(prefix="insightbench", suite="insightbench_v1"),
        "text2vis": _factoid_rows(prefix="text2vis", suite="text2vis_v1"),
    }


def _metadata(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": _SOURCE,
        "reference_sut": _REFERENCE_SUT,
    }
    if extra:
        metadata.update(extra)
    return metadata


def _sql_rows(*, prefix: str, suite: str) -> list[FixtureRow]:
    return [
        _sql_row(task_id=f"{prefix}-offline-sql-{idx}", suite=suite, query_idx=idx)
        for idx in range(1, 6)
    ]


def _sql_row(*, task_id: str, suite: str, query_idx: int) -> FixtureRow:
    return {
        "task_id": task_id,
        "item": {
            "suite": suite,
            "query": {"question": f"Return row {query_idx}."},
            "ground_truth": {"sql": "SELECT 1 AS answer"},
            "metadata": _metadata({"offline_mode": "missing_sql_engine"}),
        },
        "result": {
            "output_kind": "sql",
            "output": {"sql": "SELECT 1 AS answer"},
        },
        "baseline_score": 0.0,
    }


def _factoid_rows(*, prefix: str, suite: str) -> list[FixtureRow]:
    answers = ["42", "North", "3.14", "Alpha; Beta", "Option B"]
    rows = [
        _factoid_row(
            task_id=f"{prefix}-factoid-pass-{idx}",
            suite=suite,
            gold=answer,
            candidate=answer,
            baseline_score=1.0,
        )
        for idx, answer in enumerate(answers[:4], start=1)
    ]
    rows.append(
        _factoid_row(
            task_id=f"{prefix}-factoid-fail-5",
            suite=suite,
            gold=answers[4],
            candidate="Option C",
            baseline_score=0.0,
        )
    )
    return rows


def _factoid_row(
    *,
    task_id: str,
    suite: str,
    gold: str,
    candidate: str,
    baseline_score: float,
) -> FixtureRow:
    return {
        "task_id": task_id,
        "item": {
            "suite": suite,
            "query": {"question": "What is the expected answer?"},
            "ground_truth": {"answer": gold},
            "metadata": _metadata(),
        },
        "result": {
            "output_kind": "answer",
            "output": {"answer": candidate},
        },
        "baseline_score": baseline_score,
    }


def _rubric_rows(*, prefix: str, suite: str) -> list[FixtureRow]:
    return [
        _rubric_row(task_id=f"{prefix}-offline-rubric-{idx}", suite=suite) for idx in range(1, 6)
    ]


def _rubric_row(*, task_id: str, suite: str) -> FixtureRow:
    return {
        "task_id": task_id,
        "item": {
            "suite": suite,
            "query": {"question": "Summarize the dashboard evidence."},
            "ground_truth": {"report": "The answer cites the key evidence."},
            "metadata": _metadata(
                {
                    "offline_mode": "missing_judge_cache",
                    "rubric": {
                        "criteria": [
                            {
                                "name": "groundedness",
                                "description": "Uses only supplied evidence.",
                            }
                        ]
                    },
                }
            ),
        },
        "result": {
            "output_kind": "report",
            "output": {"report": "The answer cites the key evidence."},
        },
        "baseline_score": 0.0,
    }


def _dsbench_dm_rows() -> list[FixtureRow]:
    rows = [
        _dsbench_dm_row(task_id=f"dsbench-dm-rpg-pass-{idx}", passed=True) for idx in range(1, 5)
    ]
    rows.append(_dsbench_dm_row(task_id="dsbench-dm-rpg-fail-5", passed=False))
    return rows


def _dsbench_dm_row(*, task_id: str, passed: bool) -> FixtureRow:
    csv_text = "id,target\n1,10\n2,20\n"
    pred_csv = csv_text if passed else "id,target\n1,0\n2,0\n"
    return {
        "task_id": task_id,
        "item": {
            "suite": "dsbench_dm_v1",
            "query": {"question": "Predict target values."},
            "ground_truth": {
                "test_csv": csv_text,
                "target_col": "target",
                "metric": "rmse",
                "baseline_score": 10.0,
                "best_score": 0.0,
            },
            "metadata": _metadata(),
        },
        "result": {
            "output_kind": "csv",
            "output": {"predictions_csv": pred_csv},
        },
        "baseline_score": 1.0 if passed else 0.0,
    }
