"""Text2Vis adapter: score the chart's backing data, not rendered pixels."""

from __future__ import annotations

import csv
import json
from io import StringIO
from typing import TYPE_CHECKING, Any

from beacon_benchmarks.base.adapter import BenchmarkMetadata, EvalItemDraft
from beacon_benchmarks.text2vis.download import (
    MIRROR_URL,
    PUBLIC_URL,
    TEXT2VIS_PLACEHOLDER_SHA256,
    download_text2vis,
)

if TYPE_CHECKING:
    from pathlib import Path

SUITE = "text2vis_v1"
DATASET_VERSION = "v1-2025"


class Text2VisAdapter:
    """Adapter for Text2Vis data-grounded chart tasks."""

    metadata = BenchmarkMetadata(
        name="text2vis",
        version="1.0",
        suite=SUITE,
        license="MIT",
        public_source=PUBLIC_URL,
        public_source_sha256=TEXT2VIS_PLACEHOLDER_SHA256,
        internal_mirror=MIRROR_URL,
        size_mb=35,
        is_large=False,
        item_metadata_schema={
            "qid": "string",
            "chart_type": "string",
        },
    )

    def download(self, dest: Path, include_large: bool = False) -> Path:
        """Download Text2Vis source data."""
        return download_text2vis(dest, include_large=include_large)

    def _data_root(self, raw_root: Path) -> Path:
        data_root = raw_root / "Text2Vis" / "data"
        if data_root.exists():
            return data_root
        candidates = list(raw_root.glob("Text2Vis-*/data"))
        if candidates:
            return candidates[0]
        raise FileNotFoundError(f"Text2Vis data directory not found under {raw_root}")

    def preprocess(self, raw_root: Path) -> list[EvalItemDraft]:
        """Build eval-item drafts from Text2Vis JSON instances."""
        data_root = self._data_root(raw_root)
        drafts: list[EvalItemDraft] = []
        for path in sorted(data_root.glob("*.json")):
            task = json.loads(path.read_text(encoding="utf-8"))
            ground_truth = dict(task["ground_truth"])
            data_table_csv = str(
                task.get("data_table_csv") or ground_truth.get("data_table_csv") or ""
            )
            if data_table_csv and "data_table" not in ground_truth:
                ground_truth["data_table"] = _csv_to_rows(data_table_csv)

            drafts.append(
                EvalItemDraft(
                    suite=SUITE,
                    dataset_version=DATASET_VERSION,
                    query={"question": task["question"]},
                    context={
                        "data_table_csv": data_table_csv,
                        "instance_path": str(path),
                    },
                    ground_truth=ground_truth,
                    ground_truth_meta={"source": "Text2Vis", "qid": task["qid"]},
                    metadata={
                        "qid": task["qid"],
                        "chart_type": ground_truth.get("chart_type", "unknown"),
                    },
                    item_external_id=task["qid"],
                )
            )
        return drafts

    def register_graders(self, registry: Any) -> list[Any]:
        """Register data-grounded chart and factoid answer graders."""
        from beacon_graders.graders import DabstepAnswerMatcher, Text2VisDataGroundedGrader

        data_grader: Any = Text2VisDataGroundedGrader()
        data_grader.name = "text2vis.data_grounded"
        data_grader.suite_filter = SUITE

        answer_grader: Any = DabstepAnswerMatcher()
        answer_grader.name = "text2vis.factoid.answer"
        answer_grader.suite_filter = SUITE
        answer_grader.handle_not_applicable = False
        answer_grader.numeric_rel_tol = 1e-4
        answer_grader.numeric_abs_tol = 1e-4
        answer_grader.string_similarity_threshold = 0.95

        registry.register(data_grader)
        registry.register(answer_grader)
        return [data_grader, answer_grader]

    def ingest(self, raw_root: Path, **_: Any) -> dict[str, Any]:
        """Text2Vis ships JSON task files and does not require database ingest."""
        self._data_root(raw_root)
        return {}


def _csv_to_rows(table_csv: str) -> list[dict[str, str]]:
    reader = csv.DictReader(StringIO(table_csv))
    return [dict(row) for row in reader]
