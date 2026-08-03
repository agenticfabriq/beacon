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


class ConflictingSolutionDeclarationError(BeaconStorageError):
    """A registered version was re-declared with different layers.

    A version's declared layers are part of what its scores mean: they are what
    the attribution engine ablates one at a time, so changing them retroactively
    reinterprets every comparison already drawn against that version. Silently
    accepting the new declaration would do exactly that, so the push is refused
    and the runner is told to publish a new version instead.
    """

    code = "conflicting_solution_declaration"

    def __init__(self, message: str, *, registered: list[str], declared: list[str]) -> None:
        super().__init__(message)
        self.registered = registered
        self.declared = declared
