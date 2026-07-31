"""Integration tests for the DSBench-DM adapter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def test_adapter_registered() -> None:
    from beacon_benchmarks import ADAPTERS

    assert "dsbench_dm" in ADAPTERS
    assert ADAPTERS["dsbench_dm"].metadata.suite == "dsbench_dm_v1"


def test_preprocess_emits_csv_modeling_drafts(tmp_path: Path) -> None:
    from beacon_benchmarks.dsbench_dm.adapter import DSBenchDMAdapter

    task_dir = tmp_path / "dsbench_dm" / "data_modeling" / "data" / "task_001"
    task_dir.mkdir(parents=True)
    (task_dir / "task_description.txt").write_text("Predict y for test rows.", encoding="utf-8")
    (task_dir / "train.csv").write_text("id,x,y\n1,0,1\n", encoding="utf-8")
    (task_dir / "test.csv").write_text("id,x\n2,1\n", encoding="utf-8")
    (task_dir / "test_with_truth.csv").write_text("id,y\n2,0.5\n", encoding="utf-8")
    (task_dir / "answer.json").write_text(
        json.dumps(
            {
                "target_col": "y",
                "metric": "rmse",
                "baseline_score": 1.0,
                "best_score": 0.0,
            }
        ),
        encoding="utf-8",
    )

    drafts = DSBenchDMAdapter().preprocess(tmp_path / "dsbench_dm")

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.ground_truth["target_col"] == "y"
    assert draft.ground_truth["test_csv"] == "id,y\n2,0.5\n"
    assert draft.context["test_csv_path_for_input"].endswith("test.csv")
    assert draft.metadata["metric"] == "rmse"


def test_register_graders_returns_csv_rpg_grader() -> None:
    class Registry:
        def __init__(self) -> None:
            self.added: list[Any] = []

        def register(self, grader: Any) -> None:
            self.added.append(grader)

    from beacon_benchmarks.dsbench_dm.adapter import DSBenchDMAdapter

    registry = Registry()
    graders = DSBenchDMAdapter().register_graders(registry)

    assert [grader.name for grader in graders] == ["dsbench_dm.csv.rpg"]
    assert registry.added == graders
