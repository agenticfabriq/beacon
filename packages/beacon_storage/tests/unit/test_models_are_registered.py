"""Every model module must reach `Base.metadata`, or autogenerate drops it."""

from __future__ import annotations

import ast
import pathlib
import pkgutil

import beacon_storage.models as models_pkg
from beacon_storage.models import Base


def test_every_model_module_is_imported_by_the_package() -> None:
    """An unimported module is a table Alembic thinks should not exist.

    `migrations/env.py` sets `target_metadata = Base.metadata`, so a model
    whose module nothing imports is absent from the metadata while present in
    the database -- and the next `alembic revision --autogenerate` emits
    `op.drop_table(...)` for it, into a migration whose author is looking at
    unrelated changes. `regrade_events` shipped that way and was caught in
    review, not by a test.

    Asserted over the package's modules rather than a hand-kept list, so a new
    model file fails this the moment it exists.
    """
    modules = {
        name
        for _, name, _ in pkgutil.iter_modules(models_pkg.__path__)
        if not name.startswith("_") and name != "base"
    }

    # Parsed from `__init__.py`'s OWN imports, not `hasattr`. Importing a
    # submodule anywhere in the process sets it as an attribute of its parent
    # package, and a sibling test imports `models.regrade_events` directly --
    # which happens at COLLECTION, before any test runs, even when that test
    # is deselected. So `hasattr` was green for the exact deletion this test
    # exists to catch, while `migrations/env.py` imports only
    # `beacon_storage.models` and autogenerate would still drop the table.
    init = pathlib.Path(models_pkg.__file__ or "").with_name("__init__.py")
    tree = ast.parse(init.read_text(encoding="utf-8"))
    imported = {
        node.module.rsplit(".", 1)[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    missing = sorted(modules - imported)
    assert not missing, (
        f"model module(s) {missing} are not imported in models/__init__.py, so their "
        "tables are missing from Base.metadata and autogenerate would drop them"
    )


def test_the_tables_the_metadata_knows_include_the_operational_ones() -> None:
    """A spot check that the mechanism above actually populates metadata."""
    for table in ("results", "verdicts", "attributions", "regrade_events", "dataset_loads"):
        assert table in Base.metadata.tables, table
