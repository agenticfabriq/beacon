"""Process-global registry of benchmark adapters."""

from __future__ import annotations

from typing import Any

from beacon_benchmarks.base.adapter import BenchmarkAdapter, is_adapter
from beacon_benchmarks.base.errors import AdapterNotRegisteredError

ADAPTERS: dict[str, BenchmarkAdapter] = {}


def register_adapter(adapter: Any) -> None:
    """Register an adapter under its metadata name."""
    if not is_adapter(adapter):
        raise TypeError(f"{adapter!r} is not a BenchmarkAdapter")
    name = adapter.metadata.name
    if name in ADAPTERS:
        raise ValueError(f"adapter {name!r} already registered")
    ADAPTERS[name] = adapter


def get_adapter(name: str) -> BenchmarkAdapter:
    """Return a registered adapter by name."""
    if name not in ADAPTERS:
        raise AdapterNotRegisteredError(name)
    return ADAPTERS[name]


def list_adapters() -> list[str]:
    """Return sorted adapter names."""
    return sorted(ADAPTERS)
