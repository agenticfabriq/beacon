"""`beacon traces` commands backed by the REST API."""

from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any, cast

import click

from beacon_ui.cli.ctx import load_context
from beacon_ui.cli.http import http_client_from_context


@click.group()
def cli() -> None:
    """Production trace import operations."""


def _require_project_id(project: str | None) -> str:
    ctx = load_context()
    project_id = project or ctx.project_id
    if not project_id:
        raise click.ClickException("--project or BEACON_PROJECT required")
    return project_id


def _json_object(raw: str, *, line_no: int) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except JSONDecodeError as exc:
        raise click.ClickException(f"line {line_no}: invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise click.ClickException(f"line {line_no}: expected a JSON object")
    return cast("dict[str, Any]", parsed)


@cli.command("import")
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="JSONL file.",
)
@click.option("--solution-id", required=True)
@click.option("--project", default=None, help="Project ID. Defaults to BEACON_PROJECT or ctx.")
@click.option("--format", "format_kind", default="jsonl", type=click.Choice(["jsonl"]))
@click.option("--batch-size", default=100, type=int, show_default=True)
def import_traces(
    input_path: Path,
    solution_id: str,
    project: str | None,
    format_kind: str,
    batch_size: int,
) -> None:
    """Upload production traces from a JSONL file to the Beacon API."""
    if format_kind != "jsonl":
        raise click.ClickException("only jsonl is supported")

    project_id = _require_project_id(project)
    client = http_client_from_context(load_context())
    total = 0
    lines = input_path.read_text(encoding="utf-8").splitlines()
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        record = _json_object(line, line_no=line_no)
        record["solution_id"] = solution_id
        record["project_id"] = project_id
        client.post("/v1/traces", record)
        total += 1
        if batch_size > 0 and total % batch_size == 0:
            click.echo(f"Uploaded {total} traces...")

    click.echo(f"Imported {total} trace(s).")
