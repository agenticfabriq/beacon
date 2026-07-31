"""Download orchestration for benchmark assets."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import requests

from beacon_benchmarks.base.errors import (
    DownloadFailedError,
    LargeAssetSkippedError,
    ShaMismatchError,
)
from beacon_benchmarks.base.mirror import HttpMirror

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class DownloadSpec:
    """Static details needed to fetch one benchmark artifact."""

    name: str
    version: str
    public_source: str
    public_source_sha256: str
    internal_mirror: str | None
    size_mb: int
    is_large: bool


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest for a file."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_verify(path: Path, expected: str) -> None:
    """Raise when a file's SHA-256 digest differs from the expected value."""
    actual = sha256_file(path)
    if actual != expected:
        raise ShaMismatchError(f"{path}: expected {expected}, got {actual}")


def _download_http(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with dest.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)


def download(spec: DownloadSpec, *, dest: Path, include_large: bool = False) -> Path:
    """Download a benchmark artifact, verifying SHA-256 before returning."""
    if spec.is_large and not include_large:
        raise LargeAssetSkippedError(
            f"{spec.name} is {spec.size_mb} MB; pass --include-large to fetch"
        )

    errors: list[str] = []
    try:
        log.info("Downloading %s from %s", spec.name, spec.public_source)
        _download_http(spec.public_source, dest)
        sha256_verify(dest, spec.public_source_sha256)
        return dest
    except Exception as exc:  # noqa: BLE001 - any public failure falls back to mirror
        errors.append(f"public: {exc}")
        if dest.exists():
            dest.unlink()

    if spec.internal_mirror:
        try:
            log.info("Falling back to mirror %s", spec.internal_mirror)
            HttpMirror().pull(spec.internal_mirror, dest)
            sha256_verify(dest, spec.public_source_sha256)
            return dest
        except Exception as exc:  # noqa: BLE001 - aggregate mirror failure below
            errors.append(f"mirror: {exc}")
            if dest.exists():
                dest.unlink()

    raise DownloadFailedError(f"download {spec.name!r} failed: {'; '.join(errors)}")
