"""beacon registry items list / promote CLI integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_registry.items import ItemService
from beacon_registry.types import EvalItemTier
from beacon_ui.cli.main import app as cli_app
from click.testing import CliRunner
from test_cli_teams import configure_cli_env  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from beacon_storage.models.tenancy import Team
    from fastapi.testclient import TestClient
    from pytest import MonkeyPatch
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _World(Protocol):
    alice_key: str
    acme_team_id: UUID
    chat_to_data_id: UUID


def _alice_id(session: Session) -> UUID:
    user = UserService(session).upsert_from_oidc(
        OidcClaims(subject="alice", email="alice@example.com", name="Alice")
    )
    return user.id


def test_items_list_prints_summary(
    monkeypatch: MonkeyPatch,
    db_url: str,
    engine: Engine,
    session: Session,
    alice_team_membership: Team,
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    user_id = _alice_id(session)
    ItemService(session).create_item(
        tier=EvalItemTier.HUMAN_VERIFIED,
        suite="bird",
        team_id=alice_team_membership.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "?"},
        gold_answer={},
        item_metadata={},
        created_by=user_id,
    )
    session.commit()

    _ = engine
    from beacon_runner.cli import main

    result = CliRunner().invoke(
        main,
        [
            "registry",
            "items",
            "list",
            "--suite",
            "bird",
            "--tier",
            "human_verified",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "human_verified" in result.output
    assert "bird" in result.output


def test_promote_advances_tier(
    monkeypatch: MonkeyPatch,
    db_url: str,
    engine: Engine,
    session: Session,
    alice_team_membership: Team,
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    user_id = _alice_id(session)
    item_id = ItemService(session).create_item(
        tier=EvalItemTier.MODEL_PROPOSED,
        suite="bird",
        team_id=alice_team_membership.id,
        solution_id=None,
        dataset_version="v",
        item_input={"q": "?"},
        gold_answer=None,
        item_metadata={},
        created_by=user_id,
    )
    session.commit()

    _ = engine
    from beacon_runner.cli import main

    result = CliRunner().invoke(
        main,
        [
            "registry",
            "promote",
            str(item_id),
            "--tier",
            "execution_confirmed",
            "--reason",
            "3 SUTs converged",
            "--as",
            "alice@example.com",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "execution_confirmed" in result.output


def test_registry_items_list_uses_rest(
    api_client: TestClient,
    world: _World,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    configure_cli_env(tmp_path, monkeypatch, api_client, world.alice_key)
    monkeypatch.setenv("BEACON_TEAM", str(world.acme_team_id))

    result = CliRunner().invoke(
        cli_app,
        ["registry", "items", "list", "--format", "json"],
    )

    assert result.exit_code == 0, result.stdout
    assert "bird_minidev_v2" in result.stdout


def test_registry_queue_uses_rest(
    api_client: TestClient,
    world: _World,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    configure_cli_env(tmp_path, monkeypatch, api_client, world.alice_key)

    result = CliRunner().invoke(
        cli_app,
        [
            "registry",
            "queue",
            "--project",
            str(world.chat_to_data_id),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "item_id" in result.stdout
