"""Integration tests for the DSBench-DA adapter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def test_adapter_registered() -> None:
    from beacon_benchmarks import ADAPTERS

    assert "dsbench_da" in ADAPTERS
    assert ADAPTERS["dsbench_da"].metadata.suite == "dsbench_da_v1"


def test_preprocess_pairs_intro_and_xlsx(tmp_path: Path) -> None:
    from beacon_benchmarks.dsbench_da.adapter import DSBenchDAAdapter

    task_dir = tmp_path / "dsbench_da" / "data" / "task_001"
    task_dir.mkdir(parents=True)
    (task_dir / "introduction.txt").write_text("Q: choose A/B/C/D", encoding="utf-8")
    (task_dir / "data.xlsx").write_bytes(b"PK\x03\x04stub")
    (task_dir / "answer.json").write_text(
        json.dumps(
            {
                "answer": "B",
                "answer_type": "choice",
            }
        ),
        encoding="utf-8",
    )

    drafts = DSBenchDAAdapter().preprocess(tmp_path / "dsbench_da")

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.ground_truth["answer"] == "B"
    assert draft.ground_truth["answer_type"] == "choice"
    assert "data.xlsx" in draft.context["files"]


def test_register_graders_returns_matcher_and_rubric() -> None:
    class Registry:
        def __init__(self) -> None:
            self.added: list[Any] = []

        def register(self, grader: Any) -> None:
            self.added.append(grader)

    from beacon_benchmarks.dsbench_da.adapter import DSBenchDAAdapter

    registry = Registry()
    graders = DSBenchDAAdapter().register_graders(registry)
    names = {grader.name for grader in graders}

    assert "dsbench_da.factoid" in names
    assert "dsbench_da.rubric.narrative" in names
    assert registry.added == graders
