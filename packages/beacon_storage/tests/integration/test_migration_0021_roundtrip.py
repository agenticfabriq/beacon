"""0021 must round-trip: the columns and both constraints come and go."""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.integration

MIGRATIONS_DIR = "packages/beacon_storage/src/beacon_storage/migrations"
_COLUMNS = {"n_items_submitted", "n_baseline_excluded", "n_ablated_excluded", "n_compared"}
_CONSTRAINTS = {
    "ck_attribution_sample_within_submission",
    "ck_attribution_sample_non_negative",
}


def _cfg(db_url: str) -> Config:
    os.environ["DATABASE_URL"] = db_url
    config = Config(f"{MIGRATIONS_DIR}/alembic.ini")
    config.set_main_option("script_location", MIGRATIONS_DIR)
    return config


def _columns(engine: sa.Engine) -> set[str]:
    return {col["name"] for col in sa.inspect(engine).get_columns("attributions")}


def _checks(engine: sa.Engine) -> set[str]:
    return {
        name
        for c in sa.inspect(engine).get_check_constraints("attributions")
        if (name := c["name"]) is not None
    }


def test_0021_round_trips(engine: sa.Engine, db_url: str) -> None:
    config = _cfg(db_url)

    assert _columns(engine) >= _COLUMNS, "conftest upgraded to head"
    assert _checks(engine) >= _CONSTRAINTS

    command.downgrade(config, "0020_unrecorded_token_cost")
    assert not (_COLUMNS & _columns(engine)), "downgrade left columns behind"
    assert not (_CONSTRAINTS & _checks(engine)), "downgrade left constraints behind"

    command.upgrade(config, "head")
    assert _columns(engine) >= _COLUMNS
    assert _checks(engine) >= _CONSTRAINTS
