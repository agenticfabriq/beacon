"""0023 must round-trip, including the NULLS NOT DISTINCT constraint."""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.integration

MIGRATIONS_DIR = "packages/beacon_storage/src/beacon_storage/migrations"
_TABLES = {"derivations", "result_outcomes"}


def _cfg(db_url: str) -> Config:
    os.environ["DATABASE_URL"] = db_url
    config = Config(f"{MIGRATIONS_DIR}/alembic.ini")
    config.set_main_option("script_location", MIGRATIONS_DIR)
    return config


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def test_0023_round_trips(engine: sa.Engine, db_url: str) -> None:
    config = _cfg(db_url)

    assert _tables(engine) >= _TABLES, "conftest upgraded to head"

    command.downgrade(config, "0022_regrade_events")
    assert not (_tables(engine) & _TABLES), "downgrade left tables behind"

    command.upgrade(config, "head")
    assert _tables(engine) >= _TABLES


def test_the_unique_key_treats_nulls_as_equal(engine: sa.Engine) -> None:
    """Issued as raw DDL, so it is worth checking Postgres actually took it.

    Without NULLS NOT DISTINCT the no-grader derivation gets a fresh row per
    push. The constraint has no portable SQLAlchemy spelling in a migration,
    which is exactly the kind of hand-written DDL that silently does nothing.
    """
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM result_outcomes"))
        conn.execute(sa.text("DELETE FROM derivations"))
        conn.execute(
            sa.text(
                "INSERT INTO derivations (id, grader, grader_version, metric) "
                "VALUES (gen_random_uuid(), NULL, NULL, NULL)"
            )
        )
    with pytest.raises(sa.exc.IntegrityError, match="uq_derivation_key"), engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO derivations (id, grader, grader_version, metric) "
                "VALUES (gen_random_uuid(), NULL, NULL, NULL)"
            )
        )
