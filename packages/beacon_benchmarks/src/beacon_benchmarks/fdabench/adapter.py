"""FDABench-Lite adapter for SQLite-grounded report and choice tasks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from beacon_benchmarks.base.adapter import BenchmarkMetadata, EvalItemDraft
from beacon_benchmarks.fdabench.download import (
    FDABENCH_PLACEHOLDER_SHA256,
    MIRROR_URL,
    PUBLIC_URL,
    download_fdabench,
)

if TYPE_CHECKING:
    from pathlib import Path

SUITE = "fdabench_lite_v1"
DATASET_VERSION = "v1-2025-09"


class _MissingJudgeCache:
    def get_or_call(self, _request: Any) -> Any:
        """Stub that raises until FDABench receives a configured judge cache."""
        raise RuntimeError("FDABench report graders require a configured judge cache")


class FDABenchAdapter:
    """Adapter for FDABench-Lite tasks."""

    metadata = BenchmarkMetadata(
        name="fdabench",
        version="1.0",
        suite=SUITE,
        license="Apache-2.0",
        public_source=PUBLIC_URL,
        public_source_sha256=FDABENCH_PLACEHOLDER_SHA256,
        internal_mirror=MIRROR_URL,
        size_mb=90,
        is_large=False,
        item_metadata_schema={
            "task_id": "string",
            "category": "string",
            "sqlite_db": "string",
        },
    )

    def download(self, dest: Path, include_large: bool = False) -> Path:
        """Download FDABench source data."""
        return download_fdabench(dest, include_large=include_large)

    def _lite_dir(self, raw_root: Path) -> Path:
        lite_dir = raw_root / "FDABench-Lite"
        if lite_dir.exists():
            return lite_dir
        candidates = list(raw_root.glob("FDAbench-*/FDABench-Lite"))
        if candidates:
            return candidates[0]
        raise FileNotFoundError(f"FDABench-Lite not found under {raw_root}")

    def preprocess(self, raw_root: Path) -> list[EvalItemDraft]:
        """Build eval-item drafts from FDABench-Lite JSONL tasks."""
        lite_dir = self._lite_dir(raw_root)
        drafts: list[EvalItemDraft] = []
        with (lite_dir / "tasks.jsonl").open(encoding="utf-8") as file:
            for line in file:
                stripped = line.strip()
                if not stripped:
                    continue
                task = json.loads(stripped)
                sqlite_file = lite_dir / task["sqlite_path"]
                drafts.append(
                    EvalItemDraft(
                        suite=SUITE,
                        dataset_version=DATASET_VERSION,
                        query={
                            "question": task["question"],
                            "options": task.get("options", []),
                        },
                        context={
                            "sqlite_path": str(sqlite_file) if sqlite_file.exists() else None,
                            "instance_dir": str(lite_dir),
                        },
                        ground_truth=task["ground_truth"],
                        ground_truth_meta={
                            "source": "FDABench-Lite",
                            "task_id": task["task_id"],
                        },
                        metadata={
                            "task_id": task["task_id"],
                            "category": task["category"],
                            "sqlite_db": task["sqlite_path"],
                        },
                        item_external_id=task["task_id"],
                    )
                )
        return drafts

    def register_graders(self, registry: Any) -> list[Any]:
        """Register report and choice graders for FDABench-Lite."""
        from beacon_graders.graders import DabstepAnswerMatcher, HierarchicalRubricGrader

        judge_cache: Any = getattr(registry, "judge_cache", _MissingJudgeCache())
        rouge_grader: Any = HierarchicalRubricGrader(judge_cache=judge_cache)
        rouge_grader.name = "fdabench.rouge.report"
        rouge_grader.suite_filter = SUITE
        rouge_grader.rubric_path = None
        rouge_grader.scoring_mode = "rouge"
        rouge_grader.response_field = "report"
        rouge_grader.reference_field = "report"
        rouge_grader.applicable_when_metadata = {"category": "report"}

        choice_grader: Any = DabstepAnswerMatcher()
        choice_grader.name = "fdabench.factoid.choice"
        choice_grader.suite_filter = SUITE
        choice_grader.handle_not_applicable = False
        choice_grader.numeric_rel_tol = 0.0
        choice_grader.numeric_abs_tol = 0.0
        choice_grader.string_similarity_threshold = 1.0
        choice_grader.applicable_when_metadata_in = {
            "category": ["single_choice", "multiple_choice"]
        }

        registry.register(rouge_grader)
        registry.register(choice_grader)
        return [rouge_grader, choice_grader]

    def ingest(
        self,
        raw_root: Path,
        *,
        target_db_url: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Ingest each FDABench-Lite SQLite database to a dedicated schema."""
        if not target_db_url:
            return {"dbs": [], "skipped": [], "note": "no target_db_url"}

        from beacon_benchmarks.ingest.postgres import ingest_sqlite_to_postgres

        lite_dir = self._lite_dir(raw_root)
        loaded: dict[str, Any] = {}
        for sqlite_path in sorted(lite_dir.glob("*.sqlite")):
            loaded[sqlite_path.stem] = ingest_sqlite_to_postgres(
                sqlite_path=sqlite_path,
                postgres_url=target_db_url,
                target_schema=f"fdabench_{sqlite_path.stem}",
                benchmark_name="fdabench",
                dataset_version=DATASET_VERSION,
                suite=SUITE,
            )
        return {"dbs": list(loaded), "loads": loaded}
