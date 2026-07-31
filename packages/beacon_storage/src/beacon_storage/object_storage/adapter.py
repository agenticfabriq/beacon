"""ObjectStorage protocol. Implementations: LocalFsStorage, S3Storage."""

from __future__ import annotations

from typing import BinaryIO, Protocol


class ObjectStorage(Protocol):
    def put(self, key: str, stream: BinaryIO) -> None:
        """Write the bytes from ``stream`` to ``key``."""
        ...

    def get(self, key: str) -> BinaryIO:
        """Return a readable binary stream for the object stored at ``key``."""
        ...

    def exists(self, key: str) -> bool:
        """Return whether an object exists at ``key``."""
        ...

    def delete(self, key: str) -> None:
        """Remove the object stored at ``key`` if present."""
        ...

    def signed_url(self, key: str, *, expires_seconds: int) -> str:
        """Return a presigned URL granting access to ``key`` for ``expires_seconds``."""
        ...
