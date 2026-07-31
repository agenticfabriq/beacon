"""BIRD download from HuggingFace, mocked end-to-end."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from beacon_benchmarks.bird_minidev.adapter import BirdMinidevAdapter


def test_adapter_registered() -> None:
    from beacon_benchmarks import ADAPTERS

    assert "bird_minidev" in ADAPTERS
    assert ADAPTERS["bird_minidev"].metadata.suite == "bird_minidev_v2"


def test_adapter_metadata_targets_5_dbs() -> None:
    adapter = BirdMinidevAdapter()
    assert adapter.SELECTED_DBS == [
        "california_schools",
        "card_games",
        "european_football_2",
        "formula_1",
        "superhero",
    ]


def test_download_uses_huggingface(beacon_data_dir: Path, monkeypatch: Any) -> None:
    """HuggingFace snapshot_download is called with the correct repo id."""
    seen: dict[str, str] = {}

    def fake_snapshot_download(*, repo_id: str, repo_type: str, local_dir: str, **_: Any) -> str:
        seen["repo_id"] = repo_id
        seen["repo_type"] = repo_type
        seen["local_dir"] = local_dir
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        (Path(local_dir) / "mini_dev_data").mkdir(exist_ok=True)
        (Path(local_dir) / "mini_dev_data" / "mini_dev_postgresql.json").write_text(
            json.dumps([]),
            encoding="utf-8",
        )
        return local_dir

    monkeypatch.setattr(
        "beacon_benchmarks.bird_minidev.download.snapshot_download",
        fake_snapshot_download,
    )

    adapter = BirdMinidevAdapter()
    out = adapter.download(beacon_data_dir / "bird" / "v2-2025-07-22")

    assert seen["repo_id"] == "bird-bench/bird-mini-dev"
    assert seen["repo_type"] == "dataset"
    assert (out / "mini_dev_data" / "mini_dev_postgresql.json").exists()
