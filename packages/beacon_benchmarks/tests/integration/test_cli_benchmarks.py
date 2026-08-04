"""`beacon benchmarks {list, download, ingest}` CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from click.testing import CliRunner


def test_cli_list_shows_registered_adapters() -> None:
    from beacon_ui.cli.benchmarks import benchmarks_group

    runner = CliRunner()
    result = runner.invoke(benchmarks_group, ["list"])

    assert result.exit_code == 0, result.output
    assert "bird_minidev" in result.output


def test_cli_list_json_format() -> None:
    from beacon_ui.cli.benchmarks import benchmarks_group

    runner = CliRunner()
    result = runner.invoke(benchmarks_group, ["list", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    names = {adapter["name"] for adapter in data}
    assert "bird_minidev" in names
    for field in ("name", "version", "suite", "license", "size_mb", "is_large"):
        assert field in data[0]


def test_root_cli_registers_benchmarks_group() -> None:
    from beacon_runner.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["benchmarks", "list"])

    assert result.exit_code == 0, result.output
    assert "bird_minidev" in result.output


def test_cli_download_unknown_adapter_exits_nonzero() -> None:
    from beacon_ui.cli.benchmarks import benchmarks_group

    runner = CliRunner()
    result = runner.invoke(benchmarks_group, ["download", "nope"])

    assert result.exit_code != 0
    assert "not registered" in result.output.lower()


def test_cli_download_calls_adapter_download(tmp_path: Path, monkeypatch: Any) -> None:
    from beacon_ui.cli.benchmarks import benchmarks_group

    called: dict[str, Any] = {}

    def fake_download(self: Any, dest: Path, include_large: bool = False) -> Path:
        called["dest"] = Path(dest)
        called["include_large"] = include_large
        return dest

    monkeypatch.setattr(
        "beacon_benchmarks.bird_minidev.adapter.BirdMinidevAdapter.download",
        fake_download,
    )
    monkeypatch.setenv("BEACON_DATA_DIR", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(benchmarks_group, ["download", "bird_minidev"])

    assert result.exit_code == 0, result.output
    assert "benchmarks/bird_minidev/" in called["dest"].as_posix()
    assert called["include_large"] is False


def test_cli_download_include_large_flag(tmp_path: Path, monkeypatch: Any) -> None:
    from beacon_ui.cli.benchmarks import benchmarks_group

    captured: dict[str, Any] = {}

    def fake_download(self: Any, dest: Path, include_large: bool = False) -> Path:
        captured["include_large"] = include_large
        return dest

    monkeypatch.setattr(
        "beacon_benchmarks.bird_minidev.adapter.BirdMinidevAdapter.download",
        fake_download,
    )
    monkeypatch.setenv("BEACON_DATA_DIR", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(benchmarks_group, ["download", "bird_minidev", "--include-large"])

    assert result.exit_code == 0, result.output
    assert captured["include_large"] is True


def test_cli_ingest_invokes_adapter_with_target_db(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from beacon_ui.cli.benchmarks import benchmarks_group

    captured: dict[str, Any] = {}

    def fake_ingest(self: Any, raw_root: Path, **kwargs: Any) -> dict[str, Any]:
        captured["target_db_url"] = kwargs.get("target_db_url")
        captured["raw_root"] = Path(raw_root)
        return {"dbs": ["california_schools"], "loads": {}}

    monkeypatch.setattr(
        "beacon_benchmarks.bird_minidev.adapter.BirdMinidevAdapter.ingest",
        fake_ingest,
    )
    monkeypatch.setenv("BEACON_DATA_DIR", str(tmp_path))
    (tmp_path / "benchmarks" / "bird_minidev" / "v2-2025-07-22").mkdir(parents=True)
    runner = CliRunner()

    result = runner.invoke(
        benchmarks_group,
        [
            "ingest",
            "bird_minidev",
            "--target",
            "postgresql://beacon@localhost/test",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["target_db_url"] == "postgresql://beacon@localhost/test"
    assert captured["raw_root"].as_posix().endswith("benchmarks/bird_minidev/v2-2025-07-22")
