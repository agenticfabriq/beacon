"""`beacon suites ...` commands."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import click
from beacon_registry.errors import BeaconRegistryError, DuplicateSuiteError
from beacon_registry.selectors.separability_gain import SeparabilityGainSelector
from beacon_registry.suites import SuiteService
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.rls import set_current_user

if TYPE_CHECKING:
    from uuid import UUID

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
@click.option("--project", "project_id", required=True, type=click.UUID)
@click.option("--name", required=True)
@click.option("--description", default="")
@click.option(
    "--method",
    default="manual",
    type=click.Choice(["manual", "separability_gain", "handpicked"]),
)
@click.option("--as", "actor_email", required=True)
def create_suite(
    project_id: UUID,
    name: str,
    description: str,
    method: str,
    actor_email: str,
) -> None:
    """Create an empty suite in a project."""
    with _open_session() as session:
        actor = UserRepo(session).get_by_email(actor_email)
        if actor is None:
            raise click.ClickException(f"user {actor_email} not found")
        set_current_user(session, actor.id)

        project = ProjectRepo(session).get(project_id)
        if project is None:
            raise click.ClickException(f"project {project_id} not found")

        try:
            suite = SuiteService(session).create(
                project_id=project_id,
                team_id=project.team_id,
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


@suites_group.command("select")
@click.option("--project", "project_id", required=True, type=click.UUID)
@click.option("--suite", "suite_name", required=True)
@click.option("--source-suite", required=True)
@click.option("--method", default="separability", type=click.Choice(["separability"]))
@click.option("--n", required=True, type=int)
@click.option("--k", required=True, type=int)
@click.option("--lambda-difficulty", default=0.1, type=float)
@click.option("--as", "actor_email", required=True)
def select_items(
    project_id: UUID,
    suite_name: str,
    source_suite: str,
    method: str,
    n: int,
    k: int,
    lambda_difficulty: float,
    actor_email: str,
) -> None:
    """Select items into a suite via separability gain."""
    if method != "separability":
        raise click.ClickException(f"unsupported selector method: {method}")

    with _open_session() as session:
        actor = UserRepo(session).get_by_email(actor_email)
        if actor is None:
            raise click.ClickException(f"user {actor_email} not found")
        set_current_user(session, actor.id)

        project = ProjectRepo(session).get(project_id)
        if project is None:
            raise click.ClickException(f"project {project_id} not found")

        suite_service = SuiteService(session)
        if suite_service.get_by_project_and_name(project_id=project_id, name=suite_name) is None:
            raise click.ClickException(f"no suite named '{suite_name}' in project {project_id}")

        selector = SeparabilityGainSelector(
            K=k,
            N=n,
            suite=source_suite,
            lambda_difficulty=lambda_difficulty,
        )
        try:
            chosen = selector.run_against_history(
                session=session,
                project_id=project_id,
                team_id=project.team_id,
                created_by=actor.id,
                suite_name=suite_name,
            )
        except BeaconRegistryError as exc:
            session.rollback()
            raise click.ClickException(str(exc)) from exc
        session.commit()

    click.echo(f"selector chose {len(chosen)} item(s); tagged into suite '{suite_name}'")
