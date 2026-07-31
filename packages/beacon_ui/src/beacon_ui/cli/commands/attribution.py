"""`beacon attribution` commands."""

from __future__ import annotations

import click

from beacon_ui.cli.ctx import load_context
from beacon_ui.cli.formatters import format_output
from beacon_ui.cli.http import http_client_from_context


@click.group()
def cli() -> None:
    """Substrate attribution sweeps."""


def _require_project_id(project: str | None) -> str:
    ctx = load_context()
    project_id = project or ctx.project_id
    if not project_id:
        raise click.ClickException("--project or BEACON_PROJECT required")
    return project_id


@cli.command("sweep")
@click.option("--project", default=None, help="Project ID. Defaults to BEACON_PROJECT or ctx.")
@click.option("--sut", required=True, help="Solution UUID.")
@click.option("--suite", required=True, help="Suite UUID.")
def sweep(project: str | None, sut: str, suite: str) -> None:
    """Kick off a NIGHTLY_LOO attribution sweep for a solution and suite."""
    client = http_client_from_context(load_context())
    out = client.post(
        f"/v1/projects/{_require_project_id(project)}/runs",
        {"solution_id": sut, "suite_id": suite, "mode": "NIGHTLY_LOO", "config": {}},
    )
    click.echo(format_output(out, format="json"))


@cli.command("show")
@click.option("--project", default=None, help="Project ID. Defaults to BEACON_PROJECT or ctx.")
@click.option("--sut", required=True, help="Solution UUID.")
@click.option("--suite", required=True, help="Suite UUID.")
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json"]),
    show_default=True,
)
def show(project: str | None, sut: str, suite: str, fmt: str) -> None:
    """Display the latest per-layer attribution snapshot for a solution and suite."""
    client = http_client_from_context(load_context())
    out = client.get(
        f"/v1/projects/{_require_project_id(project)}/attribution",
        sut=sut,
        suite=suite,
    )
    layers = out.get("layers", []) if isinstance(out, dict) else []
    click.echo(format_output(layers, format=fmt))
