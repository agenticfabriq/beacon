"""Filesystem layout for downloaded benchmark data."""

from __future__ import annotations

import os
from pathlib import Path


def beacon_data_dir() -> Path:
    """Return the root directory for Beacon local data."""
    raw = os.environ.get("BEACON_DATA_DIR")
    if raw:
        return Path(raw)
    return Path.home() / ".beacon" / "data"


def benchmark_data_root() -> Path:
    """Return the root directory for downloaded benchmark assets."""
    return beacon_data_dir() / "benchmarks"


def benchmark_version_dir(name: str, version: str) -> Path:
    """Return the directory for one benchmark dataset version."""
    return benchmark_data_root() / name / version
