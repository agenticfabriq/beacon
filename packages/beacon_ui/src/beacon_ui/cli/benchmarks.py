"""`beacon benchmarks {list, download, ingest}` commands."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from beacon_benchmarks import ADAPTERS, get_adapter, list_adapters
from beacon_benchmarks.base.errors import (
    AdapterNotRegisteredError,
    BeaconBenchmarkError,
)
from beacon_benchmarks.base.layout import benchmark_version_dir

if TYPE_CHECKING:
    from beacon_benchmarks.base.adapter import BenchmarkAdapter


@click.group("benchmarks")
def benchmarks_group() -> None:
    """Manage Beacon benchmark adapters."""


@benchmarks_group.command("list")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Output format.",
)
def list_cmd(fmt: str) -> None:
    """List registered benchmark adapters."""
    names = list_adapters()
    if fmt == "json":
        click.echo(json.dumps([_metadata_payload(ADAPTERS[name]) for name in names], indent=2))
        return

    click.echo(f"{'NAME':<20} {'SUITE':<25} {'SIZE':<12} LICENSE")
    click.echo("-" * 72)
    for name in names:
        metadata = ADAPTERS[name].metadata
        size = f"{metadata.size_mb}MB" + (" large" if metadata.is_large else "")
        click.echo(f"{metadata.name:<20} {metadata.suite:<25} {size:<12} {metadata.license}")


@benchmarks_group.command("download")
@click.argument("name")
@click.option("--include-large", is_flag=True, help="Allow downloading large datasets.")
@click.option(
    "--dest",
    type=click.Path(path_type=Path),
    default=None,
    help="Override download destination.",
)
def download_cmd(name: str, include_large: bool, dest: Path | None) -> None:
    """Download a benchmark dataset."""
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


@benchmarks_group.command("ingest")
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
def ingest_cmd(name: str, target_db_url: str, raw_root: Path | None) -> None:
    """Ingest a benchmark's SQL data into Postgres where applicable."""
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
