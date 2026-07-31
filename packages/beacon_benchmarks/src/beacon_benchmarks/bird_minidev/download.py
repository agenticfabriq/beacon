"""Download the BIRD Mini-Dev V2 dataset from HuggingFace."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from huggingface_hub import snapshot_download

if TYPE_CHECKING:
    from pathlib import Path


log = logging.getLogger(__name__)

REPO_ID = "bird-bench/bird-mini-dev"
REPO_TYPE = "dataset"


def download_bird(dest: Path, include_large: bool = False) -> Path:
    """Materialize the BIRD Mini-Dev mirror under ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    allow_patterns = ["mini_dev_data/**", "evaluation/**", "*.md"]
    if include_large:
        allow_patterns.append("llm/**")

    log.info("Downloading BIRD Mini-Dev V2 to %s", dest)
    snapshot_download(
        repo_id=REPO_ID,
        repo_type=REPO_TYPE,
        local_dir=str(dest),
        allow_patterns=allow_patterns,
    )
    return dest
