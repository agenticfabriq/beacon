"""Domain errors raised by the storage layer."""

from __future__ import annotations


class BeaconStorageError(Exception):
    """Base class for storage-layer domain errors."""

    code: str = "storage_error"


class DuplicateRunError(BeaconStorageError):
    """A run with this identity already exists.

    ``runs`` is unique on
    ``(project_id, solution_id, suite, dataset_version, mode, pass_idx,
    parent_sweep_id, sweep_arm)`` with ``NULLS NOT DISTINCT``, so re-running the
    same suite and solution at the same pass without a sweep id collides. The
    raw ``IntegrityError`` surfaced from deep inside persistence and said
    nothing about what to do; this names the choice.
    """

    code = "duplicate_run"
