"""Download the DABStep dataset from HuggingFace."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from huggingface_hub import snapshot_download

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

REPO_ID = "adyen/DABstep"
REPO_TYPE = "dataset"


def download_dabstep(dest: Path, include_large: bool = False) -> Path:
    """Materialize DABStep under ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    if include_large:
        log.debug("DABStep is not gated as a large benchmark; fetching normal snapshot")
    log.info("Downloading DABStep to %s", dest)
    snapshot_download(
        repo_id=REPO_ID,
        repo_type=REPO_TYPE,
        local_dir=str(dest),
        allow_patterns=["data/**", "context/**", "*.md", "*.csv", "*.json", "*.jsonl"],
    )
    return dest
