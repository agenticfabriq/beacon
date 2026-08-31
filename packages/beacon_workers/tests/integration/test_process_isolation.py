"""Static import guard for worker process isolation."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_WORKERS_ROOT = Path(__file__).resolve().parents[2] / "src" / "beacon_workers"


def _worker_subpackages() -> tuple[str, ...]:
    """Worker subpackages as they exist on disk, never as a hand-kept list.

    This was a literal `("promotion", "convergence", "retention")`, and two of
    those three had been deleted. `rglob` on a missing directory yields nothing
    rather than raising, so their parametrized cases scanned zero files, found
    zero violations and passed -- four green tests guarding workers that were
    not there, for however long it had been since they were removed.

    Reading the directory instead means a worker added tomorrow is covered
    without anyone remembering to add it, and a worker deleted tomorrow takes
    its cases with it rather than leaving them passing over nothing.
    """
    if not _WORKERS_ROOT.is_dir():
        # Raise rather than return (): an empty tuple would parametrize to no
        # cases at all, which reads as a pass. This is the failure mode the
        # whole function exists to remove, so it must not reappear here.
        msg = f"workers package not found at {_WORKERS_ROOT}; these scans would be vacuous"
        raise AssertionError(msg)
    return tuple(
        sorted(
            child.name
            for child in _WORKERS_ROOT.iterdir()
            if child.is_dir() and (child / "__init__.py").exists()
        )
    )


_WORKER_SUBPACKAGES = _worker_subpackages()
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


def _other_workers(worker: str) -> tuple[str, ...]:
    """Every OTHER worker that exists -- the actual cross-import invariant.

    This was a hand-kept dict mapping each worker to the others, and it had
    gone stale in the direction that cannot fail: `retention` was checked for
    imports of `promotion` and `convergence`, both deleted, so the one
    surviving case compared against packages that could not exist.

    Deriving it keeps the same shape as the worker list itself. A literal here
    fails open twice over -- a worker added tomorrow gets no entry and checks
    nothing, and a worker deleted tomorrow leaves the others checking for it
    forever.
    """
    return tuple(other for other in _WORKER_SUBPACKAGES if other != worker)


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


def _scanned(root: Path) -> list[Path]:
    """Python files under ``root``, refusing to return nothing.

    An import-isolation test that scans zero files reports "no violations" and
    means "no evidence". The two are indistinguishable in the assertion that
    follows, so the emptiness has to fail here instead.
    """
    files = sorted(root.rglob("*.py"))
    assert files, f"scanned nothing under {root} -- an empty scan is not a pass"
    return files


def test_the_worker_list_is_read_from_disk_and_is_not_empty() -> None:
    """A scan over an empty list of workers would pass every test below it."""
    assert _WORKER_SUBPACKAGES, "no worker subpackages found; the scans would be vacuous"


@pytest.mark.parametrize("worker", _WORKER_SUBPACKAGES)
def test_no_forbidden_top_level_imports(worker: str) -> None:
    worker_dir = _WORKERS_ROOT / worker
    scanned = _scanned(worker_dir)
    violations: list[tuple[str, str]] = []
    for py_file in scanned:
        imports = _collect_imports(py_file)
        for import_name in imports & _FORBIDDEN_TOP_LEVEL:
            violations.append((str(py_file.relative_to(_WORKERS_ROOT.parent)), import_name))

    assert not violations, f"forbidden imports in {worker}: {violations}"


@pytest.mark.parametrize("worker", _WORKER_SUBPACKAGES)
def test_no_cross_worker_imports(worker: str) -> None:
    worker_dir = _WORKERS_ROOT / worker
    forbidden_subpackages = _other_workers(worker)
    if not forbidden_subpackages:
        # One worker package means there is nothing to cross-import, so this
        # cannot fail -- and a test that cannot fail must say "not tested"
        # rather than report a pass. That distinction is the whole subject of
        # this file.
        pytest.skip(f"{worker} is the only worker package; no cross-import is possible")
    violations: list[tuple[str, str]] = []
    for py_file in _scanned(worker_dir):
        source = py_file.read_text(encoding="utf-8")
        for subpackage in forbidden_subpackages:
            import_path = f"beacon_workers.{subpackage}"
            if import_path in source:
                violations.append((str(py_file.relative_to(_WORKERS_ROOT.parent)), import_path))

    assert not violations, f"cross-worker imports detected: {violations}"


def test_workers_only_depend_on_data_packages_and_worker_infra() -> None:
    violations: list[tuple[str, str]] = []
    for py_file in _scanned(_WORKERS_ROOT):
        imports = _collect_imports(py_file)
        for import_name in imports - _ALLOWED_TOP_LEVEL:
            violations.append((str(py_file.relative_to(_WORKERS_ROOT.parent)), import_name))

    assert not violations, f"unexpected imports: {violations}"
