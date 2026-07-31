"""WatermarkManager roundtrip against real Postgres."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_storage.db import make_session_factory
from beacon_workers.watermark import WatermarkManager

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def test_watermark_roundtrip(engine: Engine) -> None:
    factory = make_session_factory(engine)
    team_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()

    with factory() as session:
        manager = WatermarkManager(session, worker_name="promotion", team_id=team_id)
        assert manager.fetch_since() is None
        manager.advance(last_processed_id=first_id)
        session.commit()

    with factory() as session:
        manager = WatermarkManager(session, worker_name="promotion", team_id=team_id)
        assert manager.fetch_since() == first_id
        manager.advance(last_processed_id=second_id)
        session.commit()

    with factory() as session:
        manager = WatermarkManager(session, worker_name="promotion", team_id=team_id)
        assert manager.fetch_since() == second_id


def test_watermark_does_not_advance_on_rollback(engine: Engine) -> None:
    factory = make_session_factory(engine)
    team_id = uuid4()

    with factory() as session:
        manager = WatermarkManager(session, worker_name="promotion", team_id=team_id)
        manager.advance(last_processed_id=uuid4())
        session.rollback()

    with factory() as session:
        manager = WatermarkManager(session, worker_name="promotion", team_id=team_id)
        assert manager.fetch_since() is None
