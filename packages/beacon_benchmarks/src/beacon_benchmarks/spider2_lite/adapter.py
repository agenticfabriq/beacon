"""Spider 2.0 Lite adapter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from beacon_benchmarks.base.adapter import BenchmarkMetadata, EvalItemDraft
from beacon_benchmarks.spider2_lite.credentials import (
    route_task_to_dialect,
    secret_refs_for_dialect,
)
from beacon_benchmarks.spider2_lite.download import (
    SPIDER2_PLACEHOLDER_SHA256,
    download_spider2_lite,
)

if TYPE_CHECKING:
    from pathlib import Path

SUITE = "spider2_lite_v1"
DATASET_VERSION = "v1-2025-05"


def _missing_engine_factory(_item: Any) -> Any:
    raise RuntimeError("Spider2 SQL grader requires a configured engine factory")


class Spider2LiteAdapter:
    """Adapter for Spider 2.0 Lite SQL tasks across warehouse dialects."""

    metadata = BenchmarkMetadata(
        name="spider2_lite",
        version="1.0",
        suite=SUITE,
        license="Apache-2.0",
        public_source="https://github.com/xlang-ai/Spider2",
        public_source_sha256=SPIDER2_PLACEHOLDER_SHA256,
        internal_mirror=None,
        size_mb=80,
        is_large=False,
        item_metadata_schema={
            "instance_id": "string",
            "db_id": "string",
            "dialect": "string",
            "difficulty": "string",
        },
    )

    def download(self, dest: Path, include_large: bool = False) -> Path:
        """Download the Spider 2.0 Lite source data."""
        return download_spider2_lite(dest, include_large=include_large)

    def preprocess(self, raw_root: Path) -> list[EvalItemDraft]:
        """Build eval-item drafts from a Spider2 Lite JSONL manifest."""
        manifest = raw_root / "spider2-lite" / "spider2-lite.jsonl"
        if not manifest.exists():
            manifest = raw_root / "Spider2" / "spider2-lite" / "spider2-lite.jsonl"

        drafts: list[EvalItemDraft] = []
        with manifest.open(encoding="utf-8") as file:
            for line in file:
                stripped = line.strip()
                if not stripped:
                    continue
                task = json.loads(stripped)
                route = route_task_to_dialect(task)
                drafts.append(
                    EvalItemDraft(
                        suite=SUITE,
                        dataset_version=DATASET_VERSION,
                        query={
                            "question": task["question"],
                            "db": task["db_id"],
                            "external_knowledge": task.get("external_knowledge", ""),
                        },
                        context={
                            "dialect": route.value,
                            "db_id": task["db_id"],
                            "secret_refs": secret_refs_for_dialect(route),
                        },
                        ground_truth={
                            "gold_sql": task["gold_sql"],
                            "dialect": route.value,
                        },
                        ground_truth_meta={
                            "source": "Spider2-Lite",
                            "instance_id": task["instance_id"],
                        },
                        metadata={
                            "instance_id": task["instance_id"],
                            "db_id": task["db_id"],
                            "dialect": route.value,
                            "difficulty": task.get("difficulty", "unknown"),
                        },
                        difficulty=task.get("difficulty"),
                        item_external_id=task["instance_id"],
                    )
                )
        return drafts

    def register_graders(self, registry: Any) -> list[Any]:
        """Register the execution-grounded SQL grader for Spider2."""
        from beacon_graders.graders import ExecutionGroundedSqlGrader

        engine_factory = getattr(registry, "engine_factory", _missing_engine_factory)
        grader: Any = ExecutionGroundedSqlGrader(engine_factory=engine_factory)
        grader.name = "spider2_lite.exec_sql"
        grader.suite_filter = SUITE
        registry.register(grader)
        return [grader]

    def ingest(
        self,
        raw_root: Path,
        *,
        target_db_url: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Ingest SQLite tasks; remote-warehouse tasks use secret refs."""
        if not target_db_url:
            return {
                "dbs": [],
                "skipped": [],
                "note": "no target_db_url; SQLite ingest skipped",
            }

        from beacon_benchmarks.ingest.postgres import ingest_sqlite_to_postgres

        sqlite_root = raw_root / "Spider2" / "spider2-lite" / "resource" / "databases" / "sqlite"
        if not sqlite_root.exists():
            sqlite_root = raw_root / "spider2-lite" / "resource" / "databases" / "sqlite"
        if not sqlite_root.exists():
            return {"dbs": [], "skipped": ["all-sqlite (missing dir)"], "loads": {}}

        loaded: dict[str, dict[str, Any]] = {}
        skipped: list[str] = []
        for db_dir in sorted(sqlite_root.iterdir()):
            if not db_dir.is_dir():
                continue
            sqlite_file = db_dir / f"{db_dir.name}.sqlite"
            if not sqlite_file.exists():
                skipped.append(db_dir.name)
                continue
            loaded[db_dir.name] = ingest_sqlite_to_postgres(
                sqlite_path=sqlite_file,
                postgres_url=target_db_url,
                target_schema=f"spider2_{db_dir.name}",
                benchmark_name="spider2_lite",
                dataset_version=DATASET_VERSION,
                suite=SUITE,
            )
        return {"dbs": list(loaded), "skipped": skipped, "loads": loaded}
