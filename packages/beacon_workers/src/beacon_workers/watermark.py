"""Watermark helper for worker tick loops."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from beacon_storage.repository.worker_state import WorkerStateRepo

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class WatermarkManager:
    """Wrap worker_state with canonical fetch and advance operations."""

    def __init__(
        self,
        session: Session,
        *,
        worker_name: str,
        team_id: UUID | None,
    ) -> None:
        self.session = session
        self.worker_name = worker_name
        self.team_id = team_id
        self.repo = WorkerStateRepo(session)

    def fetch_since(self) -> UUID | None:
        """Return the last processed id for this worker/team, creating state if absent."""
        state = self.repo.get_or_create(
            worker_name=self.worker_name,
            team_id=self.team_id,
        )
        return state.last_processed_id

    def advance(self, *, last_processed_id: UUID) -> None:
        """Move the watermark forward to the given last processed id."""
        self.repo.advance(
            worker_name=self.worker_name,
            team_id=self.team_id,
            last_processed_id=last_processed_id,
            last_processed_at=datetime.now(UTC),
        )

    def record_error(self, message: str) -> None:
        """Increment the error counter and store a truncated error message."""
        self.repo.increment_error(
            worker_name=self.worker_name,
            team_id=self.team_id,
            message=message[:1000],
        )
