"""Tests for the process-global benchmark adapter registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from beacon_benchmarks.base.adapter import BenchmarkMetadata, EvalItemDraft
from beacon_benchmarks.base.errors import AdapterNotRegisteredError
from beacon_benchmarks.base.registry import ADAPTERS, get_adapter, list_adapters, register_adapter

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class _FakeAdapter:
    metadata = BenchmarkMetadata(
        name="fake",
        version="v1",
        suite="fake_v1",
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


@pytest.fixture(autouse=True)
def _clear_registry() -> Iterator[None]:
    saved = dict(ADAPTERS)
    ADAPTERS.clear()
    yield
    ADAPTERS.clear()
    ADAPTERS.update(saved)


def test_register_then_get() -> None:
    adapter = _FakeAdapter()
    register_adapter(adapter)
    assert get_adapter("fake") is adapter


def test_register_rejects_duplicate() -> None:
    register_adapter(_FakeAdapter())
    with pytest.raises(ValueError, match="already registered"):
        register_adapter(_FakeAdapter())


def test_register_rejects_non_adapter() -> None:
    class NotAdapter:
        metadata = None

    with pytest.raises(TypeError, match="not a BenchmarkAdapter"):
        register_adapter(NotAdapter())


def test_get_unknown_raises() -> None:
    with pytest.raises(AdapterNotRegisteredError):
        get_adapter("nope")


def test_list_adapters_returns_sorted_names() -> None:
    class A(_FakeAdapter):
        metadata = _FakeAdapter.metadata.__class__(
            **{**_FakeAdapter.metadata.__dict__, "name": "z_one"}
        )

    class B(_FakeAdapter):
        metadata = _FakeAdapter.metadata.__class__(
            **{**_FakeAdapter.metadata.__dict__, "name": "a_two"}
        )

    register_adapter(A())
    register_adapter(B())
    assert list_adapters() == ["a_two", "z_one"]
