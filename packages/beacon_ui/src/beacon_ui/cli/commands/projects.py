"""`beacon projects` commands."""

from __future__ import annotations

from typing import Any

import click

from beacon_ui.cli.ctx import load_context
from beacon_ui.cli.formatters import format_output
from beacon_ui.cli.http import http_client_from_context


@click.group()
def cli() -> None:
    """Project operations."""


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


@cli.command("list")
@click.option("--team", default=None, help="Team ID. Defaults to BEACON_TEAM or ctx.")
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json"]),
    show_default=True,
)
def list_projects(team: str | None, fmt: str) -> None:
    """List projects visible to the caller in the given team."""
    client = http_client_from_context(load_context())
    body = client.get("/v1/projects", team_id=_require_team_id(team))
    click.echo(format_output(_rows(body, key="projects"), format=fmt))


@cli.command("create")
@click.option("--name", required=True)
@click.option("--description", default="")
@click.option("--team", default=None, help="Team ID. Defaults to BEACON_TEAM or ctx.")
def create_project(name: str, description: str, team: str | None) -> None:
    """Create a new project in the given team."""
    client = http_client_from_context(load_context())
    body = {"name": name, "description": description or None}
    out = client.post("/v1/projects", body, team_id=_require_team_id(team))
    click.echo(format_output(out, format="json"))


@cli.command("archive")
@click.argument("project_id")
def archive_project(project_id: str) -> None:
    """Archive a project by ID."""
    client = http_client_from_context(load_context())
    client.delete(f"/v1/projects/{project_id}")
    click.echo(f"Archived project {project_id}.")


@cli.group("members")
def members() -> None:
    """Manage project members."""


@members.command("add")
@click.option("--project", required=True, help="Project ID.")
@click.option("--user", required=True, help="User ID.")
@click.option("--role", default="project_contributor", show_default=True)
def add_member(project: str, user: str, role: str) -> None:
    """Grant a user a project role."""
    client = http_client_from_context(load_context())
    out = client.post(
        f"/v1/projects/{project}/members",
        {"user_id": user, "role": role},
    )
    click.echo(format_output(out, format="json"))


@cli.group("solutions")
def solutions() -> None:
    """Manage project solutions."""


@solutions.command("add")
@click.option("--project", required=True, help="Project ID.")
@click.option("--sut", required=True, help="Solution ID.")
def add_solution(project: str, sut: str) -> None:
    """Attach a solution to a project."""
    client = http_client_from_context(load_context())
    out = client.post(
        f"/v1/projects/{project}/solutions",
        {"solution_id": sut},
    )
    click.echo(format_output(out, format="json"))
