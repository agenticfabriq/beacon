"""`beacon suites ...` commands."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import click
from beacon_registry.errors import DuplicateSuiteError
from beacon_registry.suites import SuiteService
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.rls import set_current_user

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _open_session() -> Session:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise click.ClickException("DATABASE_URL is not set")
    return make_session_factory(make_engine(database_url))()


@click.group("suites")
def suites_group() -> None:
    """Suite management commands."""


@suites_group.command("create")
@click.option("--team", "team_name", required=True)
@click.option("--name", required=True)
@click.option("--description", default="")
@click.option(
    "--method",
    default="manual",
    type=click.Choice(["manual", "separability_gain", "handpicked"]),
)
@click.option("--as", "actor_email", required=True)
def create_suite(
    team_name: str,
    name: str,
    description: str,
    method: str,
    actor_email: str,
) -> None:
    """Create an empty benchmark owned by a team."""
    with _open_session() as session:
        actor = UserRepo(session).get_by_email(actor_email)
        if actor is None:
            raise click.ClickException(f"user {actor_email} not found")
        set_current_user(session, actor.id)

        team = TeamRepo(session).get_by_name(team_name)
        if team is None:
            raise click.ClickException(f"team {team_name} not found")

        try:
            suite = SuiteService(session).create(
                team_id=team.id,
                name=name,
                description=description,
                method=method,
                suite_metadata={},
                created_by=actor.id,
            )
        except DuplicateSuiteError as exc:
            raise click.ClickException(str(exc)) from exc
        session.commit()

        click.echo(f"created suite '{suite.name}' (id={suite.id})")
