"""TraceIngestService: persist SDK trace payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from beacon_storage.repository.production_traces import ProductionTraceRepo

from beacon_registry.types import TraceIngestRequest, TraceIngestResult

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class TraceIngestService:
    """Persist one production_traces row per SDK trace payload."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = ProductionTraceRepo(session)

    def ingest(
        self,
        *,
        request: TraceIngestRequest,
        team_id: UUID,
        api_key_id: UUID | None,
    ) -> TraceIngestResult:
        """Persist an SDK trace payload as a production_traces row for the team."""
        production_trace = self.repo.create(
            team_id=team_id,
            project_id=request.project_id,
            solution_id=request.solution_id,
            api_key_id=api_key_id,
            item_input=request.item_input,
            item_output=request.item_output,
            trace_payload=request.trace,
            trace_metadata=request.metadata,
            is_eval_candidate=request.is_eval_candidate,
            derived_trace_id=None,
        )
        return TraceIngestResult(
            production_trace_id=production_trace.id,
            derived_trace_id=None,
            created_at=production_trace.created_at or datetime.now(UTC),
        )
