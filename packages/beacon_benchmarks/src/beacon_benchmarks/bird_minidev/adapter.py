"""BIRD Mini-Dev V2 adapter."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from beacon_benchmarks.base.adapter import BenchmarkMetadata, EvalItemDraft
from beacon_benchmarks.bird_minidev.download import download_bird

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


SELECTED_DBS = (
    "california_schools",
    "card_games",
    "european_football_2",
    "formula_1",
    "superhero",
)


def _missing_engine_factory(_item: Any) -> Any:
    raise RuntimeError("BIRD SQL grader requires a configured engine factory")


class BirdMinidevAdapter:
    """Adapter shell for BIRD Mini-Dev V2."""

    SELECTED_DBS = list(SELECTED_DBS)

    metadata = BenchmarkMetadata(
        name="bird_minidev",
        version="2.0",
        suite="bird_minidev_v2",
        license="CC-BY-SA-4.0",
        public_source="https://huggingface.co/datasets/bird-bench/bird-mini-dev",
        public_source_sha256="b" + ("1" * 63),
        internal_mirror=None,
        size_mb=85,
        is_large=False,
        item_metadata_schema={
            "db_id": "string",
            "question_id": "int",
            "difficulty": "string",
            "evidence": "string",
            "translator": "string",
        },
    )

    def download(self, dest: Path, include_large: bool = False) -> Path:
        """Download the public BIRD Mini-Dev V2 dataset."""
        return download_bird(dest, include_large=include_large)

    def preprocess(self, raw_root: Path) -> list[EvalItemDraft]:
        """Build eval-item drafts from downloaded BIRD JSON."""
        module = import_module("beacon_benchmarks.bird_minidev.golden")
        build_drafts = cast(
            "Callable[..., list[EvalItemDraft]]",
            module.build_drafts,
        )
        return build_drafts(raw_root, selected_dbs=self.SELECTED_DBS)

    def register_graders(self, registry: Any) -> list[Any]:
        """Register the execution-grounded SQL grader for BIRD."""
        from beacon_graders.graders import ExecutionGroundedSqlGrader

        engine_factory = getattr(registry, "engine_factory", _missing_engine_factory)
        grader: Any = ExecutionGroundedSqlGrader(engine_factory=engine_factory)
        grader.name = "bird_minidev_v2.exec_sql"
        grader.suite_filter = "bird_minidev_v2"
        grader.order_sensitive_when_order_by = True
        registry.register(grader)
        return [grader]

    def ingest(self, raw_root: Path, *, target_db_url: str, **_: Any) -> dict[str, Any]:
        """Ingest selected BIRD SQLite databases into Postgres."""
        module = import_module("beacon_benchmarks.bird_minidev.ingest_bird")
        ingest_bird_dbs = cast("Callable[..., dict[str, Any]]", module.ingest_bird_dbs)
        return ingest_bird_dbs(
            raw_root=raw_root,
            target_db_url=target_db_url,
            selected_dbs=self.SELECTED_DBS,
        )
