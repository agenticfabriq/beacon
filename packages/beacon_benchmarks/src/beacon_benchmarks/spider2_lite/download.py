"""Download Spider 2.0 Lite from the Spider2 GitHub repository."""

from __future__ import annotations

import logging
import shutil
import tarfile
from typing import TYPE_CHECKING

from beacon_benchmarks.base.download import DownloadSpec, download

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

SPIDER2_COMMIT = "main"
SPIDER2_TARBALL_URL = f"https://github.com/xlang-ai/Spider2/archive/{SPIDER2_COMMIT}.tar.gz"
SPIDER2_PLACEHOLDER_SHA256 = "2" * 64


def _safe_extract_tarball(tarball: Path, dest: Path) -> None:
    root = dest.resolve()
    with tarfile.open(tarball) as archive:
        for member in archive.getmembers():
            target = (dest / member.name).resolve()
            target.relative_to(root)
        archive.extractall(dest)  # noqa: S202 - tar members are path-checked above.


def download_spider2_lite(dest: Path, include_large: bool = False) -> Path:
    """Download and normalize the Spider2 repository layout."""
    dest.mkdir(parents=True, exist_ok=True)
    spec = DownloadSpec(
        name="spider2_lite",
        version="v1",
        public_source=SPIDER2_TARBALL_URL,
        public_source_sha256=SPIDER2_PLACEHOLDER_SHA256,
        internal_mirror=None,
        size_mb=80,
        is_large=False,
    )
    tarball = dest / "spider2.tar.gz"
    download(spec, dest=tarball, include_large=include_large)
    _safe_extract_tarball(tarball, dest)

    extracted = next(
        path for path in dest.iterdir() if path.is_dir() and path.name.startswith("Spider2-")
    )
    target = dest / "Spider2"
    if target.exists():
        shutil.rmtree(target)
    extracted.rename(target)
    return target
