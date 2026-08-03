"""`beacon eval` commands."""

from __future__ import annotations

import click

from beacon_ui.cli.ctx import load_context
from beacon_ui.cli.formatters import format_output
from beacon_ui.cli.http import http_client_from_context


@click.group()
def cli() -> None:
    """Register evaluation runs."""


@cli.command("run")
@click.option("--sut", required=True, help="Solution UUID.")
@click.option("--suite", required=True, help="Benchmark (suite) UUID.")
@click.option("--mode", default="EVAL", show_default=True)
@click.option("--k", default=1, type=int, show_default=True)
def run_eval(sut: str, suite: str, mode: str, k: int) -> None:
    """Register a run for the given solution and benchmark."""
    client = http_client_from_context(load_context())
    out = client.post(
        f"/v1/suites/{suite}/runs",
        {"solution_id": sut, "mode": mode, "config": {"k": k}},
    )
    click.echo(format_output(out, format="json"))
