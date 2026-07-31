"""`beacon benchmarks` commands."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from beacon_benchmarks import ADAPTERS, get_adapter, list_adapters
from beacon_benchmarks.base.errors import AdapterNotRegisteredError, BeaconBenchmarkError
from beacon_benchmarks.base.layout import benchmark_version_dir
from beacon_benchmarks.regression.fixtures import (
    default_fixture_filename,
    fixture_rows_for,
    write_fixture,
)

from beacon_ui.cli.formatters import format_output

if TYPE_CHECKING:
    from beacon_benchmarks.base.adapter import BenchmarkAdapter


@click.group()
def cli() -> None:
    """Benchmark download and ingest operations."""


@cli.command("list")
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json"]),
    show_default=True,
)
def list_benchmarks(fmt: str) -> None:
    """List registered benchmark adapters and their metadata."""
    rows = [_metadata_payload(ADAPTERS[name]) for name in list_adapters()]
    click.echo(format_output(rows, format=fmt))


@cli.command("download")
@click.argument("name")
@click.option("--include-large", is_flag=True, help="Allow downloading large datasets.")
@click.option(
    "--dest",
    type=click.Path(path_type=Path),
    default=None,
    help="Override download destination.",
)
def download(name: str, include_large: bool, dest: Path | None) -> None:
    """Download a benchmark dataset via its registered adapter."""
    try:
        adapter = get_adapter(name)
    except AdapterNotRegisteredError as exc:
        raise click.ClickException(
            f"adapter {name!r} not registered; run `beacon benchmarks list`"
        ) from exc

    if dest is None:
        dest = benchmark_version_dir(name, _dataset_version_for(adapter))

    try:
        output_path = adapter.download(dest, include_large=include_large)
    except BeaconBenchmarkError as exc:
        raise click.ClickException(f"download failed: {exc}") from exc

    click.echo(f"Downloaded {name} to {output_path}")


@cli.command("ingest")
@click.argument("name")
@click.option(
    "--target",
    "target_db_url",
    required=True,
    help="SQLAlchemy URL of the target Postgres database.",
)
@click.option(
    "--raw-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to previously downloaded data.",
)
def ingest(name: str, target_db_url: str, raw_root: Path | None) -> None:
    """Ingest a previously downloaded benchmark into the target database."""
    try:
        adapter = get_adapter(name)
    except AdapterNotRegisteredError as exc:
        raise click.ClickException(f"adapter {name!r} not registered") from exc

    if raw_root is None:
        raw_root = benchmark_version_dir(name, _dataset_version_for(adapter))
    if not raw_root.exists():
        raise click.ClickException(
            f"raw_root {raw_root} does not exist; run `beacon benchmarks download {name}`"
        )

    try:
        summary = adapter.ingest(raw_root, target_db_url=target_db_url)
    except BeaconBenchmarkError as exc:
        raise click.ClickException(f"ingest failed: {exc}") from exc

    click.echo(json.dumps(summary, indent=2, default=str))


@cli.command("make-baseline-fixture")
@click.argument("name")
@click.option(
    "--sut",
    default="built-in",
    show_default=True,
    help="Reference SUT label to record for operator context.",
)
@click.option(
    "--max-tasks",
    default=100,
    type=click.IntRange(min=1),
    show_default=True,
    help="Maximum number of representative rows to write.",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Output JSONL path. Defaults to the packaged regression fixture path.",
)
def make_baseline_fixture(name: str, sut: str, max_tasks: int, output: Path | None) -> None:
    """Write a built-in representative baseline regression fixture."""
    try:
        get_adapter(name)
    except AdapterNotRegisteredError as exc:
        raise click.ClickException(f"adapter {name!r} not registered") from exc

    rows = fixture_rows_for(name, max_tasks=max_tasks)
    for row in rows:
        metadata = row.setdefault("item", {}).setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata.setdefault("reference_sut", sut)

    if output is None:
        output = (
            Path(__file__).resolve().parents[5]
            / "beacon_benchmarks"
            / "src"
            / "beacon_benchmarks"
            / "regression"
            / "fixtures"
            / default_fixture_filename(name)
        )
    write_fixture(output, rows)
    click.echo(f"Wrote {len(rows)} rows to {output}")


def _metadata_payload(adapter: BenchmarkAdapter) -> dict[str, Any]:
    metadata = adapter.metadata
    return {
        "name": metadata.name,
        "version": metadata.version,
        "suite": metadata.suite,
        "license": metadata.license,
        "public_source": metadata.public_source,
        "internal_mirror": metadata.internal_mirror,
        "size_mb": metadata.size_mb,
        "is_large": metadata.is_large,
    }


def _dataset_version_for(adapter: BenchmarkAdapter) -> str:
    module_name = adapter.__class__.__module__
    module = importlib.import_module(module_name)
    version = getattr(module, "DATASET_VERSION", None)
    if version is None:
        package_name = module_name.rsplit(".", maxsplit=1)[0]
        try:
            sibling = importlib.import_module(f"{package_name}.golden")
        except ModuleNotFoundError:
            version = "latest"
        else:
            version = getattr(sibling, "DATASET_VERSION", "latest")
    return str(version)
