"""DSBench-DA adapter for Excel-grounded analytics tasks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from beacon_benchmarks.base.adapter import BenchmarkMetadata, EvalItemDraft
from beacon_benchmarks.dsbench_da.download import (
    DSBENCH_DA_PLACEHOLDER_SHA256,
    MIRROR_URL,
    PUBLIC_URL,
    download_dsbench_da,
)

if TYPE_CHECKING:
    from pathlib import Path

SUITE = "dsbench_da_v1"
DATASET_VERSION = "v1-2024"


class _MissingJudgeCache:
    def get_or_call(self, _request: Any) -> Any:
        """Stub that raises until DSBench-DA receives a configured judge cache."""
        raise RuntimeError("DSBench-DA narrative graders require a configured judge cache")


class DSBenchDAAdapter:
    """Adapter for DSBench data-analysis tasks."""

    metadata = BenchmarkMetadata(
        name="dsbench_da",
        version="1.0",
        suite=SUITE,
        license="MIT",
        public_source=PUBLIC_URL,
        public_source_sha256=DSBENCH_DA_PLACEHOLDER_SHA256,
        internal_mirror=MIRROR_URL,
        size_mb=120,
        is_large=True,
        item_metadata_schema={
            "task_id": "string",
            "answer_type": "string",
        },
    )

    def download(self, dest: Path, include_large: bool = False) -> Path:
        """Download DSBench-DA source data."""
        return download_dsbench_da(dest, include_large=include_large)

    def preprocess(self, raw_root: Path) -> list[EvalItemDraft]:
        """Build eval-item drafts by pairing introductions, Excel files, and answers."""
        data_root = raw_root / "data"
        if not data_root.exists():
            candidates = list(raw_root.glob("DSBench-*/data_analysis/data")) + list(
                raw_root.glob("DSBench-*/data")
            )
            if candidates:
                data_root = candidates[0]

        drafts: list[EvalItemDraft] = []
        for task_dir in sorted(data_root.iterdir()):
            if not task_dir.is_dir():
                continue
            answer_path = task_dir / "answer.json"
            intro_path = task_dir / "introduction.txt"
            if not (answer_path.exists() and intro_path.exists()):
                continue

            answer = json.loads(answer_path.read_text(encoding="utf-8"))
            files_list = sorted(path.name for path in task_dir.iterdir())
            drafts.append(
                EvalItemDraft(
                    suite=SUITE,
                    dataset_version=DATASET_VERSION,
                    query={"question": intro_path.read_text(encoding="utf-8")},
                    context={"instance_dir": str(task_dir), "files": files_list},
                    ground_truth=answer,
                    ground_truth_meta={"source": "DSBench-DA", "task_id": task_dir.name},
                    metadata={
                        "task_id": task_dir.name,
                        "answer_type": answer.get("answer_type", "unknown"),
                    },
                    item_external_id=task_dir.name,
                )
            )
        return drafts

    def register_graders(self, registry: Any) -> list[Any]:
        """Register factoid answer matching plus a narrative fallback rubric grader."""
        from beacon_graders.graders import DabstepAnswerMatcher, HierarchicalRubricGrader

        matcher: Any = DabstepAnswerMatcher()
        matcher.name = "dsbench_da.factoid"
        matcher.suite_filter = SUITE
        matcher.handle_not_applicable = False
        matcher.numeric_rel_tol = 1e-4
        matcher.numeric_abs_tol = 1e-4
        matcher.string_similarity_threshold = 0.95

        judge_cache: Any = getattr(registry, "judge_cache", _MissingJudgeCache())
        narrative: Any = HierarchicalRubricGrader(judge_cache=judge_cache)
        narrative.name = "dsbench_da.rubric.narrative"
        narrative.suite_filter = SUITE
        narrative.rubric_path = None
        narrative.scoring_mode = "point"
        narrative.response_field = "explanation"
        narrative.reference_field = "explanation"
        narrative.applicable_when_metadata = {"answer_type": "explanation"}

        registry.register(matcher)
        registry.register(narrative)
        return [matcher, narrative]

    def ingest(self, raw_root: Path, **_: Any) -> dict[str, Any]:
        """DSBench-DA is Excel-file grounded and has no DB ingest step."""
        return {}
