"""Selector protocol for subset-selection algorithms."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID


class Selector(Protocol):
    """Protocol every selector implements."""

    name: str
    version: str

    def select(self) -> list[UUID]:
        """Return the chosen item ids in selector-defined order."""
        ...
