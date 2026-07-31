"""DRBench adapter: keep data-grounded tasks and drop pure web-search tasks."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import TYPE_CHECKING, Any, cast

import yaml

from beacon_benchmarks.base.adapter import BenchmarkMetadata, EvalItemDraft
from beacon_benchmarks.drbench.download import (
    DRBENCH_PLACEHOLDER_SHA256,
    MIRROR_URL,
    PUBLIC_URL,
    download_drbench,
)

if TYPE_CHECKING:
    from pathlib import Path

SUITE = "drbench_v1"
DATASET_VERSION = "v1-2025"
_STRUCTURED_SUFFIXES = {
    ".csv",
    ".db",
    ".json",
    ".jsonl",
    ".parquet",
    ".sqlite",
    ".tsv",
    ".xls",
    ".xlsx",
}


class _MissingJudgeCache:
    def get_or_call(self, _request: Any) -> Any:
        """Stub that raises until DRBench receives a configured judge cache."""
        raise RuntimeError("DRBench graders require a configured judge cache")


def _rubric_path() -> str:
    return str(files("beacon_benchmarks.drbench.rubrics").joinpath("default.yaml"))


def _load_rubric_metadata() -> dict[str, Any]:
    raw = yaml.safe_load(
        files("beacon_benchmarks.drbench.rubrics").joinpath("default.yaml").read_text()
    )
    dimensions = cast("dict[str, Any]", raw.get("dimensions", {}) if isinstance(raw, dict) else {})
    criteria: list[dict[str, Any]] = []
    for dimension_name, dimension in dimensions.items():
        if not isinstance(dimension, dict):
            continue
        dimension_criteria = dimension.get("criteria", {})
        if not isinstance(dimension_criteria, dict):
            continue
        for criterion_name, criterion in dimension_criteria.items():
            if not isinstance(criterion, dict):
                continue
            criteria.append(
                {
                    "name": f"{dimension_name}.{criterion_name}",
                    "description": criterion.get("description", ""),
                    "scale": criterion.get("scale", [0, 5]),
                    "dimension": dimension_name,
                    "weight": dimension.get("weight", 0.0),
                }
            )
    return {"dimensions": dimensions, "criteria": criteria}


def _source_names(task: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("private_sources", "context_files"):
        values = task.get(key, [])
        if isinstance(values, list):
            names.extend(str(value) for value in values if value)
    return names


def _is_structured_source(source: str) -> bool:
    return any(source.lower().endswith(suffix) for suffix in _STRUCTURED_SUFFIXES)


def _is_data_grounded(task: dict[str, Any]) -> bool:
    if task.get("web_only"):
        return False
    return any(_is_structured_source(source) for source in _source_names(task))


class DRBenchAdapter:
    """Adapter for the DRBench data-grounded subset."""

    metadata = BenchmarkMetadata(
        name="drbench",
        version="1.0",
        suite=SUITE,
        license="Apache-2.0",
        public_source=PUBLIC_URL,
        public_source_sha256=DRBENCH_PLACEHOLDER_SHA256,
        internal_mirror=MIRROR_URL,
        size_mb=95,
        is_large=False,
        item_metadata_schema={
            "task_id": "string",
            "private_sources_count": "int",
        },
    )

    def download(self, dest: Path, include_large: bool = False) -> Path:
        """Download DRBench source data."""
        return download_drbench(dest, include_large=include_large)

    def preprocess(self, raw_root: Path) -> list[EvalItemDraft]:
        """Build eval-item drafts from data-grounded DRBench tasks."""
        tasks_dir = raw_root / "tasks"
        if not tasks_dir.exists():
            candidates = list(raw_root.glob("drbench-*/tasks"))
            if candidates:
                tasks_dir = candidates[0]

        rubric = _load_rubric_metadata()
        drafts: list[EvalItemDraft] = []
        for path in sorted(tasks_dir.glob("*.json")):
            task = json.loads(path.read_text(encoding="utf-8"))
            if not _is_data_grounded(task):
                continue

            ground_truth = dict(task["ground_truth"])
            if report := ground_truth.get("report"):
                ground_truth.setdefault("reference_insights", [report])
            ground_truth.setdefault("data_sources", _source_names(task))

            private_sources = task.get("private_sources", [])
            private_sources_count = len(private_sources) if isinstance(private_sources, list) else 0
            drafts.append(
                EvalItemDraft(
                    suite=SUITE,
                    dataset_version=DATASET_VERSION,
                    query={"question": task["question"]},
                    context={
                        "private_sources": task.get("private_sources", []),
                        "context_files": task.get("context_files", []),
                        "instance_dir": str(tasks_dir),
                    },
                    ground_truth=ground_truth,
                    ground_truth_meta={"source": "DRBench", "task_id": task["task_id"]},
                    metadata={
                        "task_id": task["task_id"],
                        "private_sources_count": private_sources_count,
                        "rubric": rubric,
                    },
                    item_external_id=task["task_id"],
                )
            )
        return drafts

    def register_graders(self, registry: Any) -> list[Any]:
        """Register DRBench rubric and citation free-text graders."""
        from beacon_graders.graders import FreeTextReferenceGrader, HierarchicalRubricGrader

        judge_cache: Any = getattr(registry, "judge_cache", _MissingJudgeCache())
        rubric_grader: Any = HierarchicalRubricGrader(judge_cache=judge_cache)
        rubric_grader.name = "drbench.rubric"
        rubric_grader.suite_filter = SUITE
        rubric_grader.rubric_path = _rubric_path()
        rubric_grader.scoring_mode = "point"
        rubric_grader.response_field = "report"
        rubric_grader.reference_field = "report"

        citation_grader: Any = FreeTextReferenceGrader(judge_cache=judge_cache)
        citation_grader.name = "drbench.freetext.citations"
        citation_grader.suite_filter = SUITE
        citation_grader.response_field = "report"
        citation_grader.reference_field = "report"
        citation_grader.criteria = ["insight_recall", "citation_correctness", "factuality"]

        registry.register(rubric_grader)
        registry.register(citation_grader)
        return [rubric_grader, citation_grader]

    def ingest(self, raw_root: Path, **_: Any) -> dict[str, Any]:
        """DRBench tasks are file-grounded and have no DB ingest step."""
        return {}
