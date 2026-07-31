"""Repository for worker_state watermarks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from beacon_storage.models.worker_state import WorkerState

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class WorkerStateRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create(self, *, worker_name: str, team_id: UUID | None) -> WorkerState:
        """Return the worker_state row for ``(worker_name, team_id)``, creating it if missing."""
        stmt = select(WorkerState).where(WorkerState.worker_name == worker_name)
        if team_id is None:
            stmt = stmt.where(WorkerState.team_id.is_(None))
        else:
            stmt = stmt.where(WorkerState.team_id == team_id)

        existing = self.session.scalar(stmt)
        if existing is not None:
            return existing

        state = WorkerState(
            worker_name=worker_name,
            team_id=team_id,
            tick_count=0,
            error_count=0,
        )
        self.session.add(state)
        self.session.flush()
        return state

    def advance(
        self,
        *,
        worker_name: str,
        team_id: UUID | None,
        last_processed_id: UUID | None,
        last_processed_at: datetime,
    ) -> None:
        """Upsert the worker watermark and bump tick_count, clearing nothing else."""
        now = datetime.now(UTC)
        stmt = pg_insert(WorkerState).values(
            worker_name=worker_name,
            team_id=team_id,
            last_processed_id=last_processed_id,
            last_processed_at=last_processed_at,
            tick_count=1,
            error_count=0,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["worker_name", "team_id"],
            set_={
                "last_processed_id": stmt.excluded.last_processed_id,
                "last_processed_at": stmt.excluded.last_processed_at,
                "tick_count": WorkerState.tick_count + 1,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        self.session.execute(stmt)

    def increment_error(
        self,
        *,
        worker_name: str,
        team_id: UUID | None,
        message: str,
    ) -> None:
        """Record an error against the worker state, bumping error_count and message."""
        now = datetime.now(UTC)
        stmt = pg_insert(WorkerState).values(
            worker_name=worker_name,
            team_id=team_id,
            tick_count=0,
            error_count=1,
            last_error_at=now,
            last_error_message=message,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["worker_name", "team_id"],
            set_={
                "error_count": WorkerState.error_count + 1,
                "last_error_at": stmt.excluded.last_error_at,
                "last_error_message": stmt.excluded.last_error_message,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        self.session.execute(stmt)
