"""Beacon benchmark adapters.

Each subpackage (currently ``bird_minidev``) ships a ``BenchmarkAdapter`` plus
a download function and a metadata schema. Adapters register themselves with
:mod:`beacon_benchmarks.base.registry` at import time so the CLI and
registry-loading code can enumerate them.
"""

from beacon_benchmarks.base.registry import (
    ADAPTERS,
    get_adapter,
    list_adapters,
    register_adapter,
)

__all__ = ["ADAPTERS", "get_adapter", "list_adapters", "register_adapter"]

from beacon_benchmarks import bird_minidev  # noqa: E402,F401 - register side effect
