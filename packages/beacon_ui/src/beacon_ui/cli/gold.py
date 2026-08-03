"""`beacon gold ...` -- bring curated gold in from the semantic layer.

Beacon does not author gold. Customer gold is curated, reviewed, versioned and
given a tolerance in the semantic layer, and arrives here as an approved export.
This is a CLI rather than an upload button on purpose: it is a per-release
operator action against a file, and putting it behind a form would invite
editing gold in the wrong place.

The importer refuses more than it accepts by design -- only ``Approved``
questions come in, and a re-import is a no-op -- so the report it prints is the
point of running it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import click
from beacon_benchmarks.ingest.golden_import import import_golden_package
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


@click.group("gold")
def gold_group() -> None:
    """Curated gold imported from the semantic layer."""


@gold_group.command("import")
@click.option(
    "--package",
    "package_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="A GoldenQuestionExportPackage JSON file.",
)
@click.option("--team", "team_name", required=True, help="Team the gold belongs to.")
@click.option("--suite", required=True, help="Suite name the questions are imported under.")
@click.option("--as", "actor_email", required=True, help="Who is importing.")
@click.option(
    "--dataset-version",
    default=None,
    help="Overrides the package's schema_version as the dataset version.",
)
def import_gold(
    package_path: Path,
    team_name: str,
    suite: str,
    actor_email: str,
    dataset_version: str | None,
) -> None:
    """Import approved golden questions from an export package."""
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"{package_path} is not valid JSON: {exc}") from exc
    if not isinstance(package, dict):
        raise click.ClickException("a golden export package must be a JSON object")

    with _open_session() as session:
        actor = UserRepo(session).get_by_email(actor_email)
        if actor is None:
            raise click.ClickException(f"user {actor_email} not found")
        set_current_user(session, actor.id)

        team = TeamRepo(session).get_by_name(team_name)
        if team is None:
            raise click.ClickException(f"team {team_name} not found")

        report = import_golden_package(
            session,
            package,
            team_id=team.id,
            suite=suite,
            created_by=actor.id,
            dataset_version=dataset_version,
        )
        session.commit()

    click.echo(f"considered      {report.considered}")
    click.echo(f"imported        {report.imported}")
    click.echo(f"already present {report.already_present}")
    click.echo(f"not approved    {report.skipped_not_approved}")
    click.echo(f"no expected     {report.skipped_no_expected_result}")
    # The refusals are the interesting half: a package that imports nothing
    # because it is all still in review should say so, not look like a no-op.
    for reason in report.skipped_reasons:
        click.echo(f"  skipped: {reason}")
