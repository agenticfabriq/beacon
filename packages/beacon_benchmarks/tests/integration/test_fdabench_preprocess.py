"""Integration tests for the FDABench-Lite adapter."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def test_adapter_registered() -> None:
    from beacon_benchmarks import ADAPTERS

    assert "fdabench" in ADAPTERS


def test_preprocess_classifies_tasks_by_category(tmp_path: Path) -> None:
    from beacon_benchmarks.fdabench.adapter import FDABenchAdapter

    root = tmp_path / "fdabench"
    lite_dir = root / "FDABench-Lite"
    lite_dir.mkdir(parents=True)
    tasks = [
        {
            "task_id": "r001",
            "category": "report",
            "question": "Summarize...",
            "sqlite_path": "db_a.sqlite",
            "ground_truth": {"report": "Revenue rose 12%."},
        },
        {
            "task_id": "s001",
            "category": "single_choice",
            "question": "Best?",
            "options": ["A: x", "B: y"],
            "sqlite_path": "db_a.sqlite",
            "ground_truth": {"answer": "B"},
        },
        {
            "task_id": "m001",
            "category": "multiple_choice",
            "question": "Which apply?",
            "options": ["A", "B", "C"],
            "sqlite_path": "db_a.sqlite",
            "ground_truth": {"answer": "A, C"},
        },
    ]
    with (lite_dir / "tasks.jsonl").open("w", encoding="utf-8") as file:
        for task in tasks:
            file.write(json.dumps(task) + "\n")

    sqlite_path = lite_dir / "db_a.sqlite"
    connection = sqlite3.connect(sqlite_path)
    connection.execute("CREATE TABLE x (a INTEGER)")
    connection.commit()
    connection.close()

    drafts = FDABenchAdapter().preprocess(root)

    assert len(drafts) == 3
    by_category = {draft.metadata["category"]: draft for draft in drafts}
    assert set(by_category) == {"report", "single_choice", "multiple_choice"}
    assert by_category["report"].ground_truth["report"].startswith("Revenue")
    assert by_category["single_choice"].ground_truth["answer"] == "B"
    assert by_category["multiple_choice"].ground_truth["answer"] == "A, C"


def test_register_graders_returns_rouge_and_matcher() -> None:
    class Registry:
        def __init__(self) -> None:
            self.added: list[Any] = []

        def register(self, grader: Any) -> None:
            self.added.append(grader)

    from beacon_benchmarks.fdabench.adapter import FDABenchAdapter

    registry = Registry()
    graders = FDABenchAdapter().register_graders(registry)
    names = {grader.name for grader in graders}

    assert "fdabench.rouge.report" in names
    assert "fdabench.factoid.choice" in names
    assert registry.added == graders
