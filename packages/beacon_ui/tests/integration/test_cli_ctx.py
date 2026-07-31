import json
from pathlib import Path

import pytest
from beacon_ui.cli.main import app
from click.testing import CliRunner

pytestmark = pytest.mark.integration

runner = CliRunner()


def test_ctx_show_starts_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACON_CTX_FILE", str(tmp_path / "c.json"))
    monkeypatch.delenv("BEACON_TEAM", raising=False)
    monkeypatch.delenv("BEACON_PROJECT", raising=False)
    monkeypatch.delenv("BEACON_API_KEY", raising=False)

    result = runner.invoke(app, ["ctx", "show"])

    assert result.exit_code == 0
    assert "api_base" in result.stdout


def test_ctx_set_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "c.json"
    monkeypatch.setenv("BEACON_CTX_FILE", str(path))

    result = runner.invoke(app, ["ctx", "set", "--team", "t1", "--project", "p1"])

    assert result.exit_code == 0, result.stdout
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["team_id"] == "t1"
    assert saved["project_id"] == "p1"
