from __future__ import annotations

import io
import shutil
from pathlib import Path
from typing import BinaryIO


class LocalFsStorage:
    """Local filesystem ObjectStorage. signed_url returns file:// URI for dev."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def put(self, key: str, stream: BinaryIO) -> None:
        """Copy ``stream`` to the file at ``key`` under the storage root."""
        with self._path(key).open("wb") as f:
            shutil.copyfileobj(stream, f)

    def get(self, key: str) -> BinaryIO:
        """Return an in-memory stream containing the file stored at ``key``."""
        return io.BytesIO(self._path(key).read_bytes())

    def exists(self, key: str) -> bool:
        """Return whether a file is present at ``key``."""
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        """Remove the file at ``key`` if present."""
        p = self._path(key)
        if p.exists():
            p.unlink()

    def signed_url(self, key: str, *, expires_seconds: int) -> str:
        """Return a ``file://`` URI pointing at ``key``; ``expires_seconds`` is ignored."""
        return f"file://{self._path(key).resolve()}"
