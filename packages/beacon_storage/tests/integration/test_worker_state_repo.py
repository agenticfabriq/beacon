"""WorkerStateRepo get/create, advance, and error semantics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_storage.repository.worker_state import WorkerStateRepo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_get_or_create_returns_zeros(session: Session) -> None:
    repo = WorkerStateRepo(session)
    team_id = uuid4()

    state = repo.get_or_create(worker_name="promotion", team_id=team_id)

    assert state.tick_count == 0
    assert state.error_count == 0
    assert state.last_processed_id is None


def test_advance_advances_watermark(session: Session) -> None:
    repo = WorkerStateRepo(session)
    team_id = uuid4()
    new_id = uuid4()

    repo.advance(
        worker_name="promotion",
        team_id=team_id,
        last_processed_id=new_id,
        last_processed_at=datetime.now(UTC),
    )
    session.commit()
    state = repo.get_or_create(worker_name="promotion", team_id=team_id)

    assert state.last_processed_id == new_id
    assert state.tick_count == 1


def test_increment_error(session: Session) -> None:
    repo = WorkerStateRepo(session)

    repo.increment_error(worker_name="promotion", team_id=None, message="boom")
    session.commit()
    state = repo.get_or_create(worker_name="promotion", team_id=None)

    assert state.error_count == 1
    assert state.last_error_message == "boom"


def test_null_team_uses_single_row(session: Session) -> None:
    repo = WorkerStateRepo(session)

    repo.advance(
        worker_name="retention",
        team_id=None,
        last_processed_id=uuid4(),
        last_processed_at=datetime.now(UTC),
    )
    repo.advance(
        worker_name="retention",
        team_id=None,
        last_processed_id=uuid4(),
        last_processed_at=datetime.now(UTC),
    )
    session.commit()
    state = repo.get_or_create(worker_name="retention", team_id=None)

    assert state.tick_count == 2
