"""`beacon traces ...` commands."""

from __future__ import annotations

import json
import os
from json import JSONDecodeError
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import click
from beacon_registry.ingestion import TraceIngestService
from beacon_registry.types import TraceIngestRequest
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.rls import set_current_user
from pydantic import ValidationError

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


def _open_session() -> Session:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise click.ClickException("DATABASE_URL is not set")
    return make_session_factory(make_engine(database_url))()


def _json_object(raw: str, *, line_no: int) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except JSONDecodeError as exc:
        raise click.ClickException(f"line {line_no}: invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise click.ClickException(f"line {line_no}: expected a JSON object")
    return cast("dict[str, Any]", parsed)


@click.group("traces")
def traces_group() -> None:
    """Trace import commands."""


@traces_group.command("import")
@click.option("--solution-id", required=True)
@click.option("--project", "project_id", required=True, type=click.UUID)
@click.option("--format", "fmt", required=True, type=click.Choice(["jsonl"]))
@click.option("--input", "input_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--as", "actor_email", required=True)
def import_traces(
    solution_id: str,
    project_id: UUID,
    fmt: str,
    input_path: str,
    actor_email: str,
) -> None:
    """Bulk-import production traces from a JSONL file."""
    if os.environ.get("BEACON_ENV", "").lower() == "prod":
        raise click.ClickException("`--as <email>` is disabled when BEACON_ENV=prod")
    if fmt != "jsonl":
        raise click.ClickException(f"unsupported format: {fmt}")

    count_ok = 0
    count_err = 0
    with _open_session() as session:
        actor = UserRepo(session).get_by_email(actor_email)
        if actor is None:
            raise click.ClickException(f"user {actor_email} not found")
        set_current_user(session, actor.id)

        project = ProjectRepo(session).get(project_id)
        if project is None:
            raise click.ClickException(f"project {project_id} not found")

        service = TraceIngestService(session)
        for line_no, raw_line in enumerate(
            Path(input_path).read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = _json_object(line, line_no=line_no)
                request = TraceIngestRequest(
                    solution_id=solution_id,
                    project_id=project_id,
                    item_input=obj.get("item_input", {}),
                    item_output=obj.get("item_output", {}),
                    trace=obj.get("trace", {}),
                    metadata=obj.get("metadata", {}),
                    is_eval_candidate=bool(obj.get("is_eval_candidate", False)),
                )
                service.ingest(request=request, team_id=project.team_id, api_key_id=None)
                count_ok += 1
            except (click.ClickException, ValidationError) as exc:
                click.echo(f"line {line_no}: skipping ({exc})", err=True)
                count_err += 1
        session.commit()

    click.echo(f"imported {count_ok} trace(s); {count_err} error(s)")
