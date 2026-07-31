"""`beacon suts` commands."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import click

from beacon_ui.cli.ctx import load_context
from beacon_ui.cli.formatters import format_output
from beacon_ui.cli.http import http_client_from_context


@click.group()
def cli() -> None:
    """SUT catalog operations."""


def _require_team_id(team: str | None) -> str:
    ctx = load_context()
    team_id = team or ctx.team_id
    if not team_id:
        raise click.ClickException("--team or BEACON_TEAM required")
    return team_id


def _rows(body: Any, *, key: str) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if isinstance(body, dict):
        value = body.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _layer_body(layer: Any) -> dict[str, Any]:
    if hasattr(layer, "model_dump"):
        data = layer.model_dump(mode="json")
        return data if isinstance(data, dict) else {}
    if isinstance(layer, dict):
        return layer
    return {
        "name": layer.name,
        "description": layer.description,
        "ablation_semantic": layer.ablation_semantic,
        "instrumentation": layer.instrumentation,
    }


def _load_sut(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("beacon_user_sut", path)
    if spec is None or spec.loader is None:
        raise click.ClickException(f"cannot load SUT module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["beacon_user_sut"] = module
    spec.loader.exec_module(module)
    if not hasattr(module, "SUT"):
        raise click.ClickException("module must define top-level `SUT` class")
    return module.SUT()


@cli.command("list")
@click.option("--team", default=None, help="Team ID. Defaults to BEACON_TEAM or ctx.")
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json"]),
    show_default=True,
)
def list_suts(team: str | None, fmt: str) -> None:
    """List SUT (solution) catalog entries for the team."""
    client = http_client_from_context(load_context())
    body = client.get(f"/v1/teams/{_require_team_id(team)}/solutions")
    click.echo(format_output(_rows(body, key="solutions"), format=fmt))


@cli.command("register")
@click.argument("sut_file", type=click.Path(exists=True, path_type=Path))
@click.option("--team", default=None, help="Team ID. Defaults to BEACON_TEAM or ctx.")
def register_sut(sut_file: Path, team: str | None) -> None:
    """Register a SUT from a Python file into the team's solution catalog."""
    sut = _load_sut(sut_file)
    identity = sut.identity()
    layers = sut.layers() if hasattr(sut, "layers") else getattr(identity, "layers", [])
    body = {
        "solution_id": identity.solution_id,
        "version": identity.version,
        "summary": getattr(identity, "summary", ""),
        "supported_modes": list(getattr(identity, "supported_modes", [])),
        "layers": [_layer_body(layer) for layer in layers],
    }

    client = http_client_from_context(load_context())
    out = client.post(f"/v1/teams/{_require_team_id(team)}/solutions", body)
    click.echo(format_output(out, format="json"))
