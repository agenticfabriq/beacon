from pathlib import Path

import pytest
from beacon_ui.cli.ctx import Context, load_context, save_context


def test_load_returns_empty_when_no_file(tmp_path: Path) -> None:
    ctx = load_context(path=tmp_path / "ctx.json")
    assert ctx == Context()


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "ctx.json"
    save_context(
        Context(team_id="t1", api_base="http://x", api_key="k"),
        path=path,
    )
    assert load_context(path=path) == Context(
        team_id="t1",
        api_base="http://x",
        api_key="k",
    )


def test_env_overrides_take_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_context(Context(team_id="from-file"), path=tmp_path / "ctx.json")
    monkeypatch.setenv("BEACON_TEAM", "from-env")

    ctx = load_context(path=tmp_path / "ctx.json")

    assert ctx.team_id == "from-env"
