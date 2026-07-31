"""Download DRBench from the public GitHub repository or a configured mirror."""

from __future__ import annotations

import tarfile
from typing import TYPE_CHECKING

from beacon_benchmarks.base.download import DownloadSpec, download
from beacon_benchmarks.base.mirror import mirror_url

if TYPE_CHECKING:
    from pathlib import Path

PUBLIC_URL = "https://github.com/ServiceNow/drbench/archive/main.tar.gz"
MIRROR_URL = mirror_url("benchmarks/DRBench.zip")
DRBENCH_PLACEHOLDER_SHA256 = "d" + "r" * 63


def _safe_extract_tarball(tarball: Path, dest: Path) -> None:
    root = dest.resolve()
    with tarfile.open(tarball) as archive:
        for member in archive.getmembers():
            target = (dest / member.name).resolve()
            target.relative_to(root)
        archive.extractall(dest)  # noqa: S202 - tar members are path-checked above.


def download_drbench(dest: Path, include_large: bool = False) -> Path:
    """Download and extract DRBench under ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    spec = DownloadSpec(
        name="drbench",
        version="v1",
        public_source=PUBLIC_URL,
        public_source_sha256=DRBENCH_PLACEHOLDER_SHA256,
        internal_mirror=MIRROR_URL,
        size_mb=95,
        is_large=False,
    )
    tarball = dest / "drbench.tar.gz"
    download(spec, dest=tarball, include_large=include_large)
    _safe_extract_tarball(tarball, dest)
    return dest
