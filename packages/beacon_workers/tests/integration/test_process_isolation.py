"""Static import guard for worker process isolation."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_WORKERS_ROOT = Path(__file__).resolve().parents[2] / "src" / "beacon_workers"
_WORKER_SUBPACKAGES = ("promotion", "convergence", "antigoodhart", "retention")
_FORBIDDEN_TOP_LEVEL = frozenset(
    {
        "beacon_ablation",
        "beacon_benchmarks",
        "beacon_iam",
        "beacon_runner",
        "beacon_sdk",
        "beacon_ui",
        "fastapi",
        "uvicorn",
    }
)
_FORBIDDEN_CROSS_WORKER = {
    "promotion": ("convergence", "antigoodhart", "retention"),
    "convergence": ("promotion", "antigoodhart", "retention"),
    "antigoodhart": ("promotion", "convergence", "retention"),
    "retention": ("promotion", "convergence", "antigoodhart"),
}
_ALLOWED_TOP_LEVEL = {
    "__future__",
    "apscheduler",
    "beacon_graders",
    "beacon_registry",
    "beacon_storage",
    "beacon_workers",
    "collections",
    "dataclasses",
    "datetime",
    "hashlib",
    "http",
    "json",
    "logging",
    "numpy",
    "pathlib",
    "pydantic",
    "pydantic_settings",
    "re",
    "signal",
    "sqlalchemy",
    "structlog",
    "sys",
    "tenacity",
    "threading",
    "time",
    "typing",
    "uuid",
}

pytestmark = pytest.mark.integration


def _collect_imports(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module.split(".")[0])
    return imports


@pytest.mark.parametrize("worker", _WORKER_SUBPACKAGES)
def test_no_forbidden_top_level_imports(worker: str) -> None:
    worker_dir = _WORKERS_ROOT / worker
    violations: list[tuple[str, str]] = []
    for py_file in worker_dir.rglob("*.py"):
        imports = _collect_imports(py_file)
        for import_name in imports & _FORBIDDEN_TOP_LEVEL:
            violations.append((str(py_file.relative_to(_WORKERS_ROOT.parent)), import_name))

    assert not violations, f"forbidden imports in {worker}: {violations}"


@pytest.mark.parametrize("worker", _WORKER_SUBPACKAGES)
def test_no_cross_worker_imports(worker: str) -> None:
    worker_dir = _WORKERS_ROOT / worker
    forbidden_subpackages = _FORBIDDEN_CROSS_WORKER[worker]
    violations: list[tuple[str, str]] = []
    for py_file in worker_dir.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        for subpackage in forbidden_subpackages:
            import_path = f"beacon_workers.{subpackage}"
            if import_path in source:
                violations.append((str(py_file.relative_to(_WORKERS_ROOT.parent)), import_path))

    assert not violations, f"cross-worker imports detected: {violations}"


def test_workers_only_depend_on_data_packages_and_worker_infra() -> None:
    violations: list[tuple[str, str]] = []
    for py_file in _WORKERS_ROOT.rglob("*.py"):
        imports = _collect_imports(py_file)
        for import_name in imports - _ALLOWED_TOP_LEVEL:
            violations.append((str(py_file.relative_to(_WORKERS_ROOT.parent)), import_name))

    assert not violations, f"unexpected imports: {violations}"
