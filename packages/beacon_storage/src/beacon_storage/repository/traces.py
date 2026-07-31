from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from beacon_storage.models.runs import Trace

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.orm import Session


class TraceRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        team_id: UUID,
        project_id: UUID,
        result_id: UUID,
        step_tree: dict[str, object],
        object_storage_uri: str | None,
    ) -> Trace:
        """Persist a trace tied to ``result_id`` and return it."""
        trace = Trace(
            team_id=team_id,
            project_id=project_id,
            result_id=result_id,
            step_tree=step_tree,
            object_storage_uri=object_storage_uri,
        )
        self.session.add(trace)
        self.session.flush()
        return trace

    def get(self, trace_id: UUID) -> Trace | None:
        """Return the trace with id ``trace_id`` or None."""
        return self.session.get(Trace, trace_id)

    def get_for_result(self, result_id: UUID) -> Trace | None:
        """Return the trace attached to ``result_id`` or None."""
        return self.session.scalar(select(Trace).where(Trace.result_id == result_id))

    def list_older_than_unsummarized(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> list[Trace]:
        """Return up to ``limit`` traces created before ``cutoff`` with no summary yet."""
        return list(
            self.session.scalars(
                select(Trace)
                .where(Trace.created_at < cutoff, Trace.summary.is_(None))
                .order_by(Trace.id)
                .limit(limit)
            )
        )

    def write_summary_clear_payload(
        self,
        *,
        trace_id: UUID,
        summary: dict[str, object],
    ) -> None:
        """Store ``summary`` on the trace and drop its object-storage payload reference."""
        trace = self.session.get(Trace, trace_id)
        if trace is None:
            return
        trace.summary = summary
        trace.object_storage_uri = None
        self.session.flush()
