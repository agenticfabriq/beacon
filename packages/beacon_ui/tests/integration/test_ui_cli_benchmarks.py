from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from beacon_benchmarks import list_adapters
from beacon_ui.cli.main import app as cli_app
from click.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def test_benchmarks_list_table_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BEACON_CTX_FILE", str(tmp_path / "c.json"))

    result = CliRunner().invoke(cli_app, ["benchmarks", "list", "--format", "table"])

    assert result.exit_code == 0, result.stdout
    assert "bird_minidev" in result.stdout


def test_benchmarks_list_json_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BEACON_CTX_FILE", str(tmp_path / "c.json"))

    result = CliRunner().invoke(cli_app, ["benchmarks", "list", "--format", "json"])

    assert result.exit_code == 0, result.stdout
    assert "bird_minidev" in result.stdout


@pytest.mark.parametrize("name", list_adapters())
def test_make_baseline_fixture_writes_jsonl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    monkeypatch.setenv("BEACON_CTX_FILE", str(tmp_path / "c.json"))
    output = tmp_path / f"{name}_sample.jsonl"

    result = CliRunner().invoke(
        cli_app,
        [
            "benchmarks",
            "make-baseline-fixture",
            name,
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.stdout
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert rows
    assert {"task_id", "item", "result", "baseline_score"} <= set(rows[0])
