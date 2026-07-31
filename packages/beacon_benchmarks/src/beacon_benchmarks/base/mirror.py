"""HTTP(S) mirror support for benchmark artifacts.

A mirror is any static file host (an S3/GCS/MinIO bucket website, plain
nginx, ...) serving the same keys as the public primary sources. Configure
``BEACON_MIRROR_BASE_URL`` to enable the fallback; when unset, adapters
carry no mirror and only the public source is tried.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from pathlib import Path


def mirror_url(key: str) -> str | None:
    """Return the mirror URL for ``key``, or ``None`` when no mirror is configured."""
    base = os.environ.get("BEACON_MIRROR_BASE_URL")
    if not base:
        return None
    return f"{base.rstrip('/')}/{key.lstrip('/')}"


class HttpMirror:
    """Pull a single mirrored object to a local path."""

    def pull(self, uri: str, dest: Path) -> None:
        """Stream the object at ``uri`` to ``dest`` in chunks."""
        if not uri.startswith(("https://", "http://")):
            raise ValueError(f"not an http(s) URI: {uri!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(uri, stream=True, timeout=120) as response:
            response.raise_for_status()
            with dest.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)
