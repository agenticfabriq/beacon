"""worker_state composite key behavior: per-worker x per-team row."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_storage.models.worker_state import WorkerState
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_worker_state_create_with_team(session: Session) -> None:
    team_id = uuid4()
    last_processed_id = uuid4()
    last_processed_at = datetime.now(UTC)

    ws = WorkerState(
        worker_name="promotion",
        team_id=team_id,
        last_processed_id=last_processed_id,
        last_processed_at=last_processed_at,
        tick_count=1,
        error_count=0,
    )

    session.add(ws)
    session.commit()

    assert ws.worker_name == "promotion"
    assert ws.team_id == team_id
    assert ws.last_processed_id == last_processed_id


def test_worker_state_null_team(session: Session) -> None:
    ws = WorkerState(worker_name="retention", team_id=None, tick_count=0, error_count=0)

    session.add(ws)
    session.commit()

    assert ws.team_id is None


def test_worker_state_unique_per_worker_team(session: Session) -> None:
    team_id = uuid4()

    session.add(WorkerState(worker_name="promotion", team_id=team_id))
    session.commit()
    session.add(WorkerState(worker_name="promotion", team_id=team_id))

    with pytest.raises(IntegrityError):
        session.commit()


def test_worker_state_unique_global_row(session: Session) -> None:
    session.add(WorkerState(worker_name="retention", team_id=None))
    session.commit()
    session.add(WorkerState(worker_name="retention", team_id=None))

    with pytest.raises(IntegrityError):
        session.commit()
