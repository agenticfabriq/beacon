"""InsightBench adapter for rubric-graded tabular analytics tasks."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import TYPE_CHECKING, Any, cast

import yaml

from beacon_benchmarks.base.adapter import BenchmarkMetadata, EvalItemDraft
from beacon_benchmarks.insightbench.download import (
    INSIGHTBENCH_PLACEHOLDER_SHA256,
    MIRROR_URL,
    download_insightbench,
)

if TYPE_CHECKING:
    from pathlib import Path

SUITE = "insightbench_v1"
DATASET_VERSION = "v1-2024-12"


class _MissingJudgeCache:
    def get_or_call(self, _request: Any) -> Any:
        """Stub that raises until InsightBench receives a configured judge cache."""
        raise RuntimeError("InsightBench graders require a configured judge cache")


def _rubric_path() -> str:
    return str(files("beacon_benchmarks.insightbench.rubrics").joinpath("default.yaml"))


def _load_rubric_metadata() -> dict[str, Any]:
    raw = yaml.safe_load(
        files("beacon_benchmarks.insightbench.rubrics").joinpath("default.yaml").read_text()
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


class InsightBenchAdapter:
    """Adapter for InsightBench narrative and structured insight tasks."""

    metadata = BenchmarkMetadata(
        name="insightbench",
        version="1.0",
        suite=SUITE,
        license="Apache-2.0",
        public_source="https://github.com/ServiceNow/insight-bench",
        public_source_sha256=INSIGHTBENCH_PLACEHOLDER_SHA256,
        internal_mirror=MIRROR_URL,
        size_mb=180,
        is_large=True,
        item_metadata_schema={
            "instance_id": "string",
            "category": "string",
            "dataset_rows": "int",
        },
    )

    def download(self, dest: Path, include_large: bool = False) -> Path:
        """Download InsightBench source data."""
        return download_insightbench(dest, include_large=include_large)

    def preprocess(self, raw_root: Path) -> list[EvalItemDraft]:
        """Build eval-item drafts from InsightBench dataset directories."""
        datasets_root = raw_root / "datasets"
        if not datasets_root.exists():
            candidates = list(raw_root.glob("insight-bench-*/datasets"))
            if candidates:
                datasets_root = candidates[0]

        rubric = _load_rubric_metadata()
        drafts: list[EvalItemDraft] = []
        for dataset_dir in sorted(datasets_root.iterdir()):
            if not dataset_dir.is_dir():
                continue
            instance_path = dataset_dir / "instance.json"
            if not instance_path.exists():
                continue
            instance = json.loads(instance_path.read_text(encoding="utf-8"))
            files_list = sorted(
                path.name
                for path in dataset_dir.iterdir()
                if path.suffix in {".csv", ".tsv", ".xlsx", ".json"}
            )
            ground_truth = dict(instance["ground_truth"])
            ground_truth.setdefault("reference_insights", ground_truth.get("insights", []))
            ground_truth.setdefault("data_sources", files_list)
            drafts.append(
                EvalItemDraft(
                    suite=SUITE,
                    dataset_version=DATASET_VERSION,
                    query={
                        "question": instance["question"],
                        "context_text": instance.get("context", ""),
                    },
                    context={
                        "files": files_list,
                        "instance_dir": str(dataset_dir),
                    },
                    ground_truth=ground_truth,
                    ground_truth_meta={
                        "source": "InsightBench",
                        "instance_id": instance["instance_id"],
                    },
                    metadata={
                        "instance_id": instance["instance_id"],
                        "category": instance.get("category", "unknown"),
                        "dataset_rows": instance.get("dataset_rows", 0),
                        "rubric": rubric,
                    },
                    item_external_id=instance["instance_id"],
                )
            )
        return drafts

    def register_graders(self, registry: Any) -> list[Any]:
        """Register rubric and free-text graders for InsightBench."""
        from beacon_graders.graders import FreeTextReferenceGrader, HierarchicalRubricGrader

        judge_cache: Any = getattr(registry, "judge_cache", _MissingJudgeCache())
        summary_grader: Any = HierarchicalRubricGrader(judge_cache=judge_cache)
        summary_grader.name = "insightbench.rubric.summary"
        summary_grader.suite_filter = SUITE
        summary_grader.rubric_path = _rubric_path()
        summary_grader.scoring_mode = "point"
        summary_grader.response_field = "summary"
        summary_grader.reference_field = "summary"

        insights_grader: Any = FreeTextReferenceGrader(judge_cache=judge_cache)
        insights_grader.name = "insightbench.freetext.insights"
        insights_grader.suite_filter = SUITE
        insights_grader.response_field = "insights"
        insights_grader.reference_field = "insights"
        insights_grader.criteria = ["recall", "factuality", "citation_correctness"]

        registry.register(summary_grader)
        registry.register(insights_grader)
        return [summary_grader, insights_grader]

    def ingest(self, raw_root: Path, **_: Any) -> dict[str, Any]:
        """InsightBench is file-grounded and has no DB ingest step."""
        return {}
