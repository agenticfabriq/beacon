"""SolutionUnderTest protocol and shipped reference SUTs.

The :class:`SolutionUnderTest` Protocol below remains importable as
``from beacon_runner.sut import SolutionUnderTest`` for backwards compatibility
with code written when this module was a single ``sut.py`` file.

Reference SUTs ship under :mod:`beacon_runner.sut.reference`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from beacon_runner.types import (
        EvalItem,
        ExecutionResult,
        Layer,
        SolutionConfig,
        SolutionIdentity,
    )


@runtime_checkable
class SolutionUnderTest(Protocol):
    """Beacon's pluggable SUT contract."""

    def identity(self) -> SolutionIdentity:
        """Return the SUT's solution_id, version, and declared layer manifest."""
        ...

    def layers(self) -> list[Layer]:
        """List the ablatable layers exposed by this SUT."""
        ...

    def validate_config(self, config: SolutionConfig) -> list[str]:
        """Return human-readable errors for an invalid ``SolutionConfig``."""
        ...

    def invoke(self, item: EvalItem, config: SolutionConfig) -> ExecutionResult:
        """Run the SUT against ``item`` under ``config`` and return its trace."""
        ...


__all__ = ["SolutionUnderTest"]
