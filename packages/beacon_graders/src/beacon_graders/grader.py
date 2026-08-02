"""Grader Protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from beacon_runner.types import EvalItem, ExecutionResult

    from beacon_graders.types import GraderKind, Verdict


@runtime_checkable
class Grader(Protocol):
    name: str
    version: str
    # What the grader produces. ``name`` is a display label that benchmark
    # adapters rewrite per suite, so composition precedence keys on this.
    kind: GraderKind

    # Graders may also declare ``metric: str | None`` -- the named metric they
    # contribute ("ex", "got_facts"). Deliberately not required here: unlike
    # ``kind``, which every grader needs because it decides composition, a
    # grader may legitimately contribute no named metric. VerdictComposer reads
    # it defensively, and the shipped graders default it to None so callers can
    # set it per instance the way benchmark adapters set ``name``.

    def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:
        """Return whether this grader can score ``item``/``result``."""
        ...

    def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:
        """Score ``item``/``result`` and emit one or more ``Verdict`` objects."""
        ...
