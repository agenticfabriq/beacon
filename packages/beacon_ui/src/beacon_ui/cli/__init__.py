"""Beacon UI CLI command groups."""

from __future__ import annotations

from beacon_ui.cli.benchmarks import benchmarks_group
from beacon_ui.cli.registry_cmds import registry_group
from beacon_ui.cli.suites_cmds import suites_group
from beacon_ui.cli.traces_cmds import traces_group

__all__ = ["benchmarks_group", "registry_group", "suites_group", "traces_group"]
