"""Download InsightBench from the ServiceNow insight-bench repository."""

from __future__ import annotations

import logging
import tarfile
from typing import TYPE_CHECKING

from beacon_benchmarks.base.download import DownloadSpec, download
from beacon_benchmarks.base.mirror import mirror_url

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

PUBLIC_URL = "https://github.com/ServiceNow/insight-bench/archive/main.tar.gz"
MIRROR_URL = mirror_url("benchmarks/InsightBench.zip")
INSIGHTBENCH_PLACEHOLDER_SHA256 = "1" * 64


def _safe_extract_tarball(tarball: Path, dest: Path) -> None:
    root = dest.resolve()
    with tarfile.open(tarball) as archive:
        for member in archive.getmembers():
            target = (dest / member.name).resolve()
            target.relative_to(root)
        archive.extractall(dest)  # noqa: S202 - tar members are path-checked above.


def download_insightbench(dest: Path, include_large: bool = False) -> Path:
    """Download and extract InsightBench."""
    dest.mkdir(parents=True, exist_ok=True)
    spec = DownloadSpec(
        name="insightbench",
        version="v1",
        public_source=PUBLIC_URL,
        public_source_sha256=INSIGHTBENCH_PLACEHOLDER_SHA256,
        internal_mirror=MIRROR_URL,
        size_mb=180,
        is_large=True,
    )
    tarball = dest / "insightbench.tar.gz"
    log.info("Downloading InsightBench to %s", dest)
    download(spec, dest=tarball, include_large=include_large)
    _safe_extract_tarball(tarball, dest)
    return dest
