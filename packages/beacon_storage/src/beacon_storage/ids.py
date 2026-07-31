"""UUID v7 generation. v7 is time-sortable; we use it everywhere for ID columns."""

from uuid import UUID

from uuid_extensions import uuid7 as _uuid7


def uuid7() -> UUID:
    """Return a freshly generated UUID v7 as a ``UUID`` object."""
    return UUID(str(_uuid7()))


def uuid7_str() -> str:
    """Return a freshly generated UUID v7 as a string."""
    return str(_uuid7())
