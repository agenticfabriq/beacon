"""DSBench-DM adapter for Kaggle-style CSV prediction tasks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from beacon_benchmarks.base.adapter import BenchmarkMetadata, EvalItemDraft
from beacon_benchmarks.dsbench_dm.csv_grader import CsvPredictionGrader
from beacon_benchmarks.dsbench_dm.download import (
    DSBENCH_DM_PLACEHOLDER_SHA256,
    MIRROR_URL,
    PUBLIC_URL,
    download_dsbench_dm,
)

if TYPE_CHECKING:
    from pathlib import Path

SUITE = "dsbench_dm_v1"
DATASET_VERSION = "v1-2024"


class DSBenchDMAdapter:
    """Adapter for DSBench data-modeling tasks."""

    metadata = BenchmarkMetadata(
        name="dsbench_dm",
        version="1.0",
        suite=SUITE,
        license="MIT",
        public_source=PUBLIC_URL,
        public_source_sha256=DSBENCH_DM_PLACEHOLDER_SHA256,
        internal_mirror=MIRROR_URL,
        size_mb=380,
        is_large=True,
        item_metadata_schema={
            "task_id": "string",
            "metric": "string",
        },
    )

    def download(self, dest: Path, include_large: bool = False) -> Path:
        """Download DSBench-DM source data."""
        return download_dsbench_dm(dest, include_large=include_large)

    def preprocess(self, raw_root: Path) -> list[EvalItemDraft]:
        """Build eval-item drafts from DSBench data-modeling task folders."""
        data_root = raw_root / "data_modeling" / "data"
        if not data_root.exists():
            candidates = list(raw_root.glob("DSBench-*/data_modeling/data"))
            if candidates:
                data_root = candidates[0]

        drafts: list[EvalItemDraft] = []
        for task_dir in sorted(data_root.iterdir()):
            if not task_dir.is_dir():
                continue
            description_path = task_dir / "task_description.txt"
            train_path = task_dir / "train.csv"
            test_path = task_dir / "test.csv"
            answer_path = task_dir / "answer.json"
            if not all(
                path.exists() for path in (description_path, train_path, test_path, answer_path)
            ):
                continue

            answer = json.loads(answer_path.read_text(encoding="utf-8"))
            truth_path = task_dir / "test_with_truth.csv"
            truth_csv = (
                truth_path.read_text(encoding="utf-8")
                if truth_path.exists()
                else test_path.read_text(encoding="utf-8")
            )
            drafts.append(
                EvalItemDraft(
                    suite=SUITE,
                    dataset_version=DATASET_VERSION,
                    query={"question": description_path.read_text(encoding="utf-8")},
                    context={
                        "instance_dir": str(task_dir),
                        "train_csv_path": str(train_path),
                        "test_csv_path_for_input": str(test_path),
                    },
                    ground_truth={
                        "test_csv": truth_csv,
                        "target_col": answer["target_col"],
                        "metric": answer["metric"],
                        "baseline_score": answer["baseline_score"],
                        "best_score": answer["best_score"],
                    },
                    ground_truth_meta={
                        "source": "DSBench-DM",
                        "task_id": task_dir.name,
                    },
                    metadata={
                        "task_id": task_dir.name,
                        "metric": answer["metric"],
                    },
                    item_external_id=task_dir.name,
                )
            )
        return drafts

    def register_graders(self, registry: Any) -> list[Any]:
        """Register the DSBench-DM CSV RPG grader."""
        grader = CsvPredictionGrader(suite_filter=SUITE)
        registry.register(grader)
        return [grader]

    def ingest(self, raw_root: Path, **_: Any) -> dict[str, Any]:
        """DSBench-DM is CSV-file grounded and has no DB ingest step."""
        return {}
