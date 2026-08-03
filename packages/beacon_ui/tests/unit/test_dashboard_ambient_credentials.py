"""The dashboard reads the same credentials as the rest of the product.

It stored auth in session state only and hardcoded localhost, so it could not
be pre-authenticated for a demo, a kiosk or an automated screenshot, and every
session began by hand-pasting a key -- while BEACON_API_KEY, BEACON_API_BASE and
the ~/.beacon/ctx.json written by `beacon login` were read by the CLI all along.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from beacon_ui.dashboard.state import DashboardState

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _state() -> DashboardState:
    return DashboardState({})


def test_an_environment_key_authenticates_without_a_sign_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BEACON_CTX_FILE", str(tmp_path / "ctx.json"))
    monkeypatch.setenv("BEACON_API_KEY", "bcn_env")

    assert _state().api_key == "bcn_env"
    assert _state().is_authenticated() is True


def test_the_context_file_supplies_a_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`beacon login` writes this file; the dashboard never read it."""
    path = tmp_path / "ctx.json"
    path.write_text(
        json.dumps({"api_key": "bcn_file", "api_base": "http://api.internal"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("BEACON_CTX_FILE", str(path))
    monkeypatch.delenv("BEACON_API_KEY", raising=False)
    monkeypatch.delenv("BEACON_API_BASE", raising=False)

    assert _state().api_key == "bcn_file"
    assert _state().api_base == "http://api.internal"


def test_an_explicit_sign_in_wins_over_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BEACON_CTX_FILE", str(tmp_path / "ctx.json"))
    monkeypatch.setenv("BEACON_API_KEY", "bcn_env")
    state = _state()

    state.set_credentials(api_key="bcn_typed", api_base="http://typed")

    assert state.api_key == "bcn_typed"
    assert state.api_base == "http://typed"


def test_signing_out_of_a_pre_authenticated_dashboard_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Otherwise the ambient key would silently sign you back in."""
    monkeypatch.setenv("BEACON_CTX_FILE", str(tmp_path / "ctx.json"))
    monkeypatch.setenv("BEACON_API_KEY", "bcn_env")
    state = _state()
    assert state.is_authenticated() is True

    state.logout()

    assert state.api_key is None
    assert state.is_authenticated() is False


def test_the_local_default_still_applies_with_nothing_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BEACON_CTX_FILE", str(tmp_path / "missing.json"))
    monkeypatch.delenv("BEACON_API_KEY", raising=False)
    monkeypatch.delenv("BEACON_API_BASE", raising=False)

    assert _state().api_key is None
    assert _state().api_base == "http://localhost:8000"
