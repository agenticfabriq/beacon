"""Beacon CLI root."""

from __future__ import annotations

import click

from beacon_ui.cli import demo as demo_cmd
from beacon_ui.cli.commands import attribution as attribution_cmd
from beacon_ui.cli.commands import benchmarks as benchmarks_cmd
from beacon_ui.cli.commands import ctx_cmd, eval_cmd
from beacon_ui.cli.commands import login as login_cmd
from beacon_ui.cli.commands import projects as projects_cmd
from beacon_ui.cli.commands import registry as registry_cmd
from beacon_ui.cli.commands import suts as suts_cmd
from beacon_ui.cli.commands import teams as teams_cmd
from beacon_ui.cli.commands import traces as traces_cmd
from beacon_ui.cli.gold import gold_group
from beacon_ui.cli.suites_cmds import suites_group


@click.group(name="beacon", help="Beacon evaluation framework CLI.")
def app() -> None:
    """Top-level CLI group."""


app.add_command(login_cmd.login, name="login")
app.add_command(ctx_cmd.cli, name="ctx")
app.add_command(teams_cmd.cli, name="teams")
app.add_command(projects_cmd.cli, name="projects")
app.add_command(suts_cmd.cli, name="suts")
app.add_command(eval_cmd.cli, name="eval")
app.add_command(attribution_cmd.cli, name="attribution")
app.add_command(registry_cmd.cli, name="registry")
app.add_command(suites_group)
app.add_command(gold_group)
app.add_command(traces_cmd.cli, name="traces")
app.add_command(benchmarks_cmd.cli, name="benchmarks")
app.add_command(demo_cmd.cli, name="demo")

cli = app


if __name__ == "__main__":  # pragma: no cover
    app()
