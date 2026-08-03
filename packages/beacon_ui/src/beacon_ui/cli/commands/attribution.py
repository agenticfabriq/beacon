"""`beacon attribution` commands."""

from __future__ import annotations

import click

from beacon_ui.cli.ctx import load_context
from beacon_ui.cli.formatters import format_output
from beacon_ui.cli.http import http_client_from_context


@click.group()
def cli() -> None:
    """Substrate attribution sweeps."""


@cli.command("sweep")
@click.option("--sut", required=True, help="Solution UUID.")
@click.option("--suite", required=True, help="Benchmark (suite) UUID.")
def sweep(sut: str, suite: str) -> None:
    """Register a NIGHTLY_LOO attribution run for a solution and benchmark."""
    client = http_client_from_context(load_context())
    out = client.post(
        f"/v1/suites/{suite}/runs",
        {"solution_id": sut, "mode": "NIGHTLY_LOO", "config": {}},
    )
    click.echo(format_output(out, format="json"))


@cli.command("show")
@click.option("--sut", required=True, help="Solution UUID.")
@click.option("--suite", required=True, help="Benchmark (suite) UUID.")
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json"]),
    show_default=True,
)
def show(sut: str, suite: str, fmt: str) -> None:
    """Display the latest per-layer attribution snapshot for a solution and benchmark."""
    client = http_client_from_context(load_context())
    out = client.get(f"/v1/suites/{suite}/attribution", sut=sut)
    layers = out.get("layers", []) if isinstance(out, dict) else []
    click.echo(format_output(layers, format=fmt))
