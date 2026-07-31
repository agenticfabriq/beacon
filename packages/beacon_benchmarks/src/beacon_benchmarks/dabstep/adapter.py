"""DABStep adapter for file-grounded factoid analytics tasks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from beacon_benchmarks.base.adapter import BenchmarkMetadata, EvalItemDraft
from beacon_benchmarks.base.mirror import mirror_url
from beacon_benchmarks.dabstep.download import download_dabstep

if TYPE_CHECKING:
    from pathlib import Path

SUITE = "dabstep_v1"
DATASET_VERSION = "v1-2025-06"
CONTEXT_FILES = (
    "acquirer_countries.csv",
    "fees.json",
    "manual.md",
    "merchant_category_codes.csv",
    "merchant_data.json",
    "payments-readme.md",
    "payments.csv",
)


class DabstepAdapter:
    """Adapter for DABStep financial analytics questions."""

    metadata = BenchmarkMetadata(
        name="dabstep",
        version="1.0",
        suite=SUITE,
        license="Apache-2.0",
        public_source="https://huggingface.co/datasets/adyen/DABstep",
        public_source_sha256="d" + ("a" * 63),
        internal_mirror=mirror_url("benchmarks/DABStep.zip"),
        size_mb=42,
        is_large=False,
        item_metadata_schema={
            "task_id": "string",
            "level": "string",
        },
    )

    def download(self, dest: Path, include_large: bool = False) -> Path:
        """Download DABStep source data."""
        return download_dabstep(dest, include_large=include_large)

    def preprocess(self, raw_root: Path) -> list[EvalItemDraft]:
        """Build eval-item drafts from DABStep JSONL task files."""
        candidates = list(raw_root.glob("tasks.jsonl")) + list(raw_root.glob("data/*.jsonl"))
        if not candidates:
            raise FileNotFoundError(f"No DABStep tasks file found under {raw_root}")

        tasks: list[dict[str, Any]] = []
        for path in candidates:
            with path.open(encoding="utf-8") as file:
                for line in file:
                    stripped = line.strip()
                    if stripped:
                        tasks.append(json.loads(stripped))

        context_dir = raw_root / "context" if (raw_root / "context").exists() else raw_root
        present_files = [name for name in CONTEXT_FILES if (context_dir / name).exists()]
        context_payload = {
            "files": present_files,
            "context_dir": str(context_dir),
        }

        drafts: list[EvalItemDraft] = []
        for task in tasks:
            task_id = str(task["task_id"])
            drafts.append(
                EvalItemDraft(
                    suite=SUITE,
                    dataset_version=DATASET_VERSION,
                    query={
                        "question": task["question"],
                        "guidelines": task.get("guidelines", ""),
                    },
                    context=dict(context_payload),
                    ground_truth={"answer": task["answer"]},
                    ground_truth_meta={"source": "DABStep"},
                    metadata={
                        "task_id": task_id,
                        "level": task.get("level", "unknown"),
                    },
                    difficulty=task.get("level"),
                    item_external_id=f"dabstep:{task_id}",
                )
            )
        return drafts

    def register_graders(self, registry: Any) -> list[Any]:
        """Register the DABStep factoid answer matcher."""
        from beacon_graders.graders import DabstepAnswerMatcher

        grader: Any = DabstepAnswerMatcher()
        grader.name = "dabstep.factoid"
        grader.suite_filter = SUITE
        grader.handle_not_applicable = True
        grader.numeric_rel_tol = 1e-4
        grader.numeric_abs_tol = 1e-4
        grader.string_similarity_threshold = 0.95
        registry.register(grader)
        return [grader]

    def ingest(self, raw_root: Path, **_: Any) -> dict[str, Any]:
        """DABStep is file-grounded and has no DB ingest step."""
        return {}
