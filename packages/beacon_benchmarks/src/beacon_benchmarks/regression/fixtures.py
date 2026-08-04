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
