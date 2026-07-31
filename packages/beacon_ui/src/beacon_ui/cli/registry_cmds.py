"""`beacon registry ...` commands."""

from __future__ import annotations

import json
import os
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any, cast

import click
from beacon_registry.errors import EvalItemNotFoundError, InvalidTierTransitionError
from beacon_registry.items import ItemService
from beacon_registry.provenance import ProvenanceService
from beacon_registry.types import ActorType, EvalItemTier
from beacon_storage.db import make_engine, make_session_factory
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


def _tier_value(tier: EvalItemTier | str | None) -> str:
    if tier is None:
        return "<initial>"
    if isinstance(tier, EvalItemTier):
        return tier.value
    return tier


def _tier_choice() -> click.Choice[str]:
    return click.Choice([tier.value for tier in EvalItemTier])


def _parse_evidence(raw: str | None) -> dict[str, Any]:
    if raw is None:
        return {}
    try:
        parsed = json.loads(raw)
    except JSONDecodeError as exc:
        raise click.ClickException(f"--evidence must be valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise click.ClickException("--evidence must be a JSON object")
    return cast("dict[str, Any]", parsed)


@click.group("registry")
def registry_group() -> None:
    """Eval-item registry operations."""


@registry_group.group("items")
def items_group() -> None:
    """Eval-item listing operations."""


@items_group.command("list")
@click.option("--suite")
@click.option("--tier", type=_tier_choice())
@click.option("--team-id", type=click.UUID)
def items_list(suite: str | None, tier: str | None, team_id: UUID | None) -> None:
    """List active eval items."""
    tier_filter = EvalItemTier(tier) if tier is not None else None
    with _open_session() as session:
        service = ItemService(session)
        if team_id is None:
            items = service.query(suite=suite, tier=tier_filter)
        else:
            items = service.query(suite=suite, tier=tier_filter, team_id=team_id)

        for item in items:
            click.echo(
                "\t".join(
                    [
                        str(item.item_id),
                        _tier_value(item.tier),
                        item.suite,
                        item.dataset_version,
                    ]
                )
            )


@registry_group.command("promote")
@click.argument("item_id", type=click.UUID)
@click.option("--tier", "new_tier", required=True, type=_tier_choice())
@click.option("--reason", required=True)
@click.option("--as", "actor_email", required=True)
@click.option("--evidence")
def promote(
    item_id: UUID,
    new_tier: str,
    reason: str,
    actor_email: str,
    evidence: str | None,
) -> None:
    """Promote an eval item to a later registry tier."""
    evidence_payload = _parse_evidence(evidence)

    with _open_session() as session:
        actor = UserRepo(session).get_by_email(actor_email)
        if actor is None:
            raise click.ClickException(f"user {actor_email} not found")
        set_current_user(session, actor.id)

        try:
            event = ProvenanceService(session).promote_tier(
                item_id=item_id,
                new_tier=EvalItemTier(new_tier),
                actor_type=ActorType.HUMAN,
                actor_id=actor_email,
                created_by=actor.id,
                reason=reason,
                evidence=evidence_payload,
            )
        except (EvalItemNotFoundError, InvalidTierTransitionError) as exc:
            session.rollback()
            raise click.ClickException(str(exc)) from exc

        session.commit()
        click.echo(
            f"promoted {event.item_id}: {_tier_value(event.prior_tier)} -> "
            f"{_tier_value(event.new_tier)}"
        )
