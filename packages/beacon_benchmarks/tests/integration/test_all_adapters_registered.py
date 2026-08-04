"""Smoke test that importing beacon_benchmarks registers all adapters."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.integration

EXPECTED_ADAPTERS = {
    "bird_minidev": "bird_minidev_v2",
}


def test_all_adapters_register() -> None:
    from beacon_benchmarks import ADAPTERS, list_adapters

    names = list_adapters()

    assert len(names) == len(EXPECTED_ADAPTERS), f"adapter count mismatch: {names}"
    assert set(names) == set(EXPECTED_ADAPTERS), (
        f"adapter set mismatch: missing={set(EXPECTED_ADAPTERS) - set(names)}, "
        f"extra={set(names) - set(EXPECTED_ADAPTERS)}"
    )
    for name, expected_suite in EXPECTED_ADAPTERS.items():
        adapter = ADAPTERS[name]
        assert adapter.metadata.name == name
        assert adapter.metadata.suite == expected_suite, (
            f"{name}: suite mismatch (got {adapter.metadata.suite!r}, expected {expected_suite!r})"
        )


def test_each_adapter_metadata_schema_uses_recognized_type_tags() -> None:
    from beacon_benchmarks import ADAPTERS

    recognized = {"string", "int", "float", "bool", "list[str]", "list[int]"}
    for name, adapter in ADAPTERS.items():
        for field, tag in adapter.metadata.item_metadata_schema.items():
            assert tag in recognized, (
                f"{name}.metadata.item_metadata_schema[{field!r}] = {tag!r} "
                "is not a recognized type tag"
            )


def test_each_adapter_registers_at_least_one_grader() -> None:
    from beacon_benchmarks import ADAPTERS

    class FakeRegistry:
        def __init__(self) -> None:
            self.added: list[Any] = []

        def register(self, grader: Any) -> None:
            self.added.append(grader)

    for name, adapter in ADAPTERS.items():
        registry = FakeRegistry()
        graders = adapter.register_graders(registry)
        assert len(graders) >= 1, f"{name}: register_graders returned no graders"
        assert len(registry.added) == len(graders), (
            f"{name}: register_graders did not call registry.register for all "
            f"returned graders ({len(graders)} returned, "
            f"{len(registry.added)} registered)"
        )


def test_each_adapter_metadata_size_consistency() -> None:
    from beacon_benchmarks import ADAPTERS

    for name, adapter in ADAPTERS.items():
        metadata = adapter.metadata
        if metadata.size_mb > 100:
            assert metadata.is_large is True, (
                f"{name}: size_mb={metadata.size_mb} but is_large=False"
            )
