"""`beacon teams` commands."""

from __future__ import annotations

from typing import Any

import click

from beacon_ui.cli.ctx import load_context
from beacon_ui.cli.formatters import format_output
from beacon_ui.cli.http import http_client_from_context


@click.group()
def cli() -> None:
    """Team operations."""


def _rows(body: Any, *, key: str) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if isinstance(body, dict):
        value = body.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


@cli.command("list")
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json"]),
    show_default=True,
)
def list_teams(fmt: str) -> None:
    """List teams visible to the caller."""
    client = http_client_from_context(load_context())
    body = client.get("/v1/teams")
    click.echo(format_output(_rows(body, key="teams"), format=fmt))
