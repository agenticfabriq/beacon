"""`beacon registry` commands backed by the REST API."""

from __future__ import annotations

from typing import Any

import click

from beacon_ui.cli.ctx import load_context
from beacon_ui.cli.formatters import format_output
from beacon_ui.cli.http import http_client_from_context


@click.group()
def cli() -> None:
    """Eval-item registry operations."""


def _rows(body: Any, *, key: str) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if isinstance(body, dict):
        value = body.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _require_project_id(project: str | None) -> str:
    ctx = load_context()
    project_id = project or ctx.project_id
    if not project_id:
        raise click.ClickException("--project or BEACON_PROJECT required")
    return project_id


@cli.group("items")
def items() -> None:
    """Inspect registry items."""


@items.command("list")
@click.option("--suite", default=None)
@click.option("--tier", default=None)
@click.option("--team", default=None, help="Team ID. Defaults to BEACON_TEAM or ctx.")
@click.option("--limit", default=50, type=int, show_default=True)
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json"]),
    show_default=True,
)
def list_items(
    suite: str | None,
    tier: str | None,
    team: str | None,
    limit: int,
    fmt: str,
) -> None:
    """List registry eval items, optionally filtered by suite, tier, or team."""
    ctx = load_context()
    params: dict[str, object] = {"limit": limit}
    if suite:
        params["suite"] = suite
    if tier:
        params["tier"] = tier
    if team or ctx.team_id:
        params["team_id"] = team or ctx.team_id

    client = http_client_from_context(ctx)
    body = client.get("/v1/registry/items", **params)
    click.echo(format_output(_rows(body, key="items"), format=fmt))


@cli.command("promote")
@click.argument("item_id")
@click.option("--tier", default="human_verified", show_default=True)
@click.option("--reason", required=True)
@click.option("--project", default=None, help="Project ID. Defaults to BEACON_PROJECT or ctx.")
def promote(item_id: str, tier: str, reason: str, project: str | None) -> None:
    """Promote a registry item to a new tier."""
    ctx = load_context()
    project_id = project or ctx.project_id
    params = {"project_id": project_id} if project_id else {}
    client = http_client_from_context(ctx)
    out = client.post(
        f"/v1/registry/items/{item_id}/promote",
        {"new_tier": tier, "reason": reason},
        **params,
    )
    click.echo(format_output(out, format="json"))


@cli.command("queue")
@click.option("--project", default=None, help="Project ID. Defaults to BEACON_PROJECT or ctx.")
@click.option("--suite", default=None)
@click.option("--limit", default=20, type=int, show_default=True)
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json"]),
    show_default=True,
)
def queue(project: str | None, suite: str | None, limit: int, fmt: str) -> None:
    """Show the project's pending review queue."""
    params: dict[str, object] = {"limit": limit}
    if suite:
        params["suite"] = suite
    client = http_client_from_context(load_context())
    body = client.get(f"/v1/projects/{_require_project_id(project)}/review-queue", **params)
    rows = [
        {
            "item_id": item.get("item_id"),
            "suite": item.get("suite"),
            "tier": item.get("tier"),
            "n_candidates": len(item.get("candidates", []))
            if isinstance(item.get("candidates"), list)
            else 0,
        }
        for item in _rows(body, key="items")
    ]
    click.echo(format_output(rows, format=fmt))
