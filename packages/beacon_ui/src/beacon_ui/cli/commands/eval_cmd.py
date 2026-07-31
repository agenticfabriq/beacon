"""`beacon eval` commands."""

from __future__ import annotations

import click

from beacon_ui.cli.ctx import load_context
from beacon_ui.cli.formatters import format_output
from beacon_ui.cli.http import http_client_from_context


@click.group()
def cli() -> None:
    """Run and gate evaluations."""


def _require_project_id(project: str | None) -> str:
    ctx = load_context()
    project_id = project or ctx.project_id
    if not project_id:
        raise click.ClickException("--project or BEACON_PROJECT required")
    return project_id


@cli.command("run")
@click.option("--project", default=None, help="Project ID. Defaults to BEACON_PROJECT or ctx.")
@click.option("--sut", required=True, help="Solution UUID.")
@click.option("--suite", required=True, help="Suite UUID.")
@click.option("--mode", default="EVAL", show_default=True)
@click.option("--k", default=1, type=int, show_default=True)
def run_eval(project: str | None, sut: str, suite: str, mode: str, k: int) -> None:
    """Queue an evaluation run for the given solution and suite."""
    client = http_client_from_context(load_context())
    out = client.post(
        f"/v1/projects/{_require_project_id(project)}/runs",
        {"solution_id": sut, "suite_id": suite, "mode": mode, "config": {"k": k}},
    )
    click.echo(format_output(out, format="json"))


@cli.command("gate")
@click.option("--project", default=None, help="Project ID. Defaults to BEACON_PROJECT or ctx.")
@click.option("--sut", required=True, help="Solution UUID.")
@click.option("--commit", required=True, help="Commit SHA.")
@click.option("--ci-url", default=None)
def gate(project: str | None, sut: str, commit: str, ci_url: str | None) -> None:
    """Run the PR gate for a commit and exit non-zero on a fail decision."""
    client = http_client_from_context(load_context())
    body: dict[str, object] = {"solution_id": sut, "commit_sha": commit}
    if ci_url:
        body["ci_run_url"] = ci_url
    out = client.post(f"/v1/projects/{_require_project_id(project)}/gate", body)
    click.echo(format_output(out, format="json"))
    if isinstance(out, dict) and out.get("decision") == "fail":
        raise click.exceptions.Exit(1)
