"""Tests for the BenchmarkAdapter protocol and metadata schema."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from beacon_benchmarks.base.adapter import (
    BenchmarkAdapter,
    BenchmarkMetadata,
    EvalItemDraft,
    is_adapter,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_metadata_required_fields() -> None:
    md = BenchmarkMetadata(
        name="dummy",
        version="v1",
        suite="dummy_v1",
        license="MIT",
        public_source="https://example.com/dummy.zip",
        public_source_sha256="0" * 64,
        internal_mirror=None,
        size_mb=12,
        is_large=False,
        item_metadata_schema={"task_id": "string", "level": "string"},
    )
    assert md.name == "dummy"
    assert md.is_large is False
    assert "task_id" in md.item_metadata_schema


def test_metadata_large_flag_required_when_size_over_100() -> None:
    with pytest.raises(ValueError):
        BenchmarkMetadata(
            name="big",
            version="v1",
            suite="big_v1",
            license="MIT",
            public_source="x",
            public_source_sha256="0" * 64,
            internal_mirror=None,
            size_mb=512,
            is_large=False,
            item_metadata_schema={},
        )


def test_eval_item_draft_round_trip() -> None:
    draft = EvalItemDraft(
        suite="x_v1",
        dataset_version="2026-01",
        query={"q": "1+1"},
        context={"file": "x.csv"},
        ground_truth={"answer": "2"},
        metadata={"task_id": "abc"},
        difficulty="easy",
    )
    assert draft.metadata == {"task_id": "abc"}


def test_is_adapter_matches_minimal_impl() -> None:
    class Mini:
        metadata = BenchmarkMetadata(
            name="m",
            version="v1",
            suite="m_v1",
            license="MIT",
            public_source="x",
            public_source_sha256="0" * 64,
            internal_mirror=None,
            size_mb=1,
            is_large=False,
            item_metadata_schema={},
        )

        def download(self, dest: Path, include_large: bool = False) -> Path:
            return dest

        def preprocess(self, raw_root: Path) -> list[EvalItemDraft]:
            return []

        def register_graders(self, registry: Any) -> list[Any]:
            return []

        def ingest(self, raw_root: Path, **kwargs: Any) -> dict[str, Any]:
            return {}

    adapter: BenchmarkAdapter = Mini()
    assert is_adapter(adapter)


def test_is_adapter_rejects_missing_method() -> None:
    class Broken:
        metadata = None

    assert is_adapter(Broken()) is False
