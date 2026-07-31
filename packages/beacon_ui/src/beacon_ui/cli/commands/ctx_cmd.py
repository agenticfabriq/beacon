"""`beacon ctx` commands."""

from __future__ import annotations

import json

import click

from beacon_ui.cli.ctx import Context, load_context, save_context


@click.group()
def cli() -> None:
    """Show, set, or clear local CLI context."""


@cli.command("show")
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json"]),
    help="Output format.",
)
def show(fmt: str) -> None:
    """Print the active CLI context (team, project, API base, masked key)."""
    ctx = load_context()
    data = {
        "team_id": ctx.team_id,
        "project_id": ctx.project_id,
        "api_base": ctx.api_base,
        "api_key": "***" if ctx.api_key else None,
    }
    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        return
    for key, value in data.items():
        click.echo(f"  {key:12} {value}")


@cli.command("set")
@click.option("--team", default=None, help="Team ID to store.")
@click.option("--project", default=None, help="Project ID to store.")
@click.option("--api-base", default=None, help="Beacon API base URL.")
def set_(team: str | None, project: str | None, api_base: str | None) -> None:
    """Persist team, project, and API base into the local CLI context."""
    ctx = load_context()
    if team is not None:
        ctx.team_id = team
    if project is not None:
        ctx.project_id = project
    if api_base is not None:
        ctx.api_base = api_base
    save_context(ctx)
    click.echo("Context saved.")


@cli.command("clear")
def clear() -> None:
    """Reset the local CLI context to defaults."""
    save_context(Context())
    click.echo("Context cleared.")
