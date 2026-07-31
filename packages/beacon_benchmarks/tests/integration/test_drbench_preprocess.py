"""Integration tests for the DRBench adapter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def test_adapter_registered() -> None:
    from beacon_benchmarks import ADAPTERS

    assert "drbench" in ADAPTERS


def test_preprocess_drops_pure_web_search_tasks(tmp_path: Path) -> None:
    from beacon_benchmarks.drbench.adapter import DRBenchAdapter

    root = tmp_path / "drbench"
    tasks_dir = root / "tasks"
    tasks_dir.mkdir(parents=True)
    tasks = [
        {
            "task_id": "t1",
            "question": "Quarterly trend?",
            "private_sources": ["q3_report.xlsx"],
            "web_only": False,
            "ground_truth": {"report": "Trend up 8%."},
        },
        {
            "task_id": "t2",
            "question": "What is the GDP of France?",
            "private_sources": [],
            "web_only": True,
            "ground_truth": {"report": "..."},
        },
        {
            "task_id": "t3",
            "question": "Combine sales report with public benchmarks.",
            "private_sources": ["sales.csv"],
            "web_only": False,
            "ground_truth": {"report": "..."},
        },
    ]
    for task in tasks:
        (tasks_dir / f"{task['task_id']}.json").write_text(
            json.dumps(task),
            encoding="utf-8",
        )

    drafts = DRBenchAdapter().preprocess(root)
    ids = {draft.metadata["task_id"] for draft in drafts}

    assert ids == {"t1", "t3"}


def test_register_graders_includes_rubric_and_freetext() -> None:
    class Registry:
        def __init__(self) -> None:
            self.added: list[Any] = []

        def register(self, grader: Any) -> None:
            self.added.append(grader)

    from beacon_benchmarks.drbench.adapter import DRBenchAdapter

    registry = Registry()
    graders = DRBenchAdapter().register_graders(registry)
    names = {grader.name for grader in graders}

    assert "drbench.rubric" in names
    assert "drbench.freetext.citations" in names
    assert registry.added == graders
