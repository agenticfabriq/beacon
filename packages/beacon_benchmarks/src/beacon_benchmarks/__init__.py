"""Beacon benchmark adapters.

Each subpackage (``bird_minidev``, ``spider2_lite``, ``dabstep``, and others)
ships a ``BenchmarkAdapter`` plus a download function and a metadata schema.
Adapters register themselves with :mod:`beacon_benchmarks.base.registry` at
import time so the CLI and registry-loading code can enumerate them.
"""

from beacon_benchmarks.base.registry import (
    ADAPTERS,
    get_adapter,
    list_adapters,
    register_adapter,
)

__all__ = ["ADAPTERS", "get_adapter", "list_adapters", "register_adapter"]

from beacon_benchmarks import (  # noqa: E402,F401 - register side effect
    bird_minidev,
    dabstep,
    drbench,
    dsbench_da,
    dsbench_dm,
    fdabench,
    insightbench,
    spider2_lite,
    text2vis,
)
