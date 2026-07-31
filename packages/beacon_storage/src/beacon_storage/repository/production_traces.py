"""ProductionTrace repository."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from beacon_storage.models.production_traces import ProductionTrace

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class ProductionTraceRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        team_id: UUID,
        project_id: UUID | None,
        solution_id: str,
        api_key_id: UUID | None,
        item_input: dict[str, Any],
        item_output: dict[str, Any],
        trace_payload: dict[str, Any],
        trace_metadata: dict[str, Any],
        is_eval_candidate: bool,
        derived_trace_id: UUID | None,
    ) -> ProductionTrace:
        """Persist a production trace row and return the saved model."""
        production_trace = ProductionTrace(
            team_id=team_id,
            project_id=project_id,
            solution_id=solution_id,
            api_key_id=api_key_id,
            item_input=item_input,
            item_output=item_output,
            trace_payload=trace_payload,
            trace_metadata=trace_metadata,
            is_eval_candidate=is_eval_candidate,
            derived_trace_id=derived_trace_id,
        )
        self.session.add(production_trace)
        self.session.flush()
        return production_trace

    def get(self, pk: UUID) -> ProductionTrace | None:
        """Return the production trace with primary key ``pk`` or None."""
        return self.session.get(ProductionTrace, pk)

    def list_unprocessed(self, *, limit: int = 1000) -> list[ProductionTrace]:
        """Return up to ``limit`` traces that have not yet been marked processed."""
        return list(
            self.session.scalars(
                select(ProductionTrace)
                .where(ProductionTrace.processed_at.is_(None))
                .order_by(ProductionTrace.created_at, ProductionTrace.id)
                .limit(limit)
            )
        )

    def list_after(
        self,
        *,
        team_id: UUID,
        after_id: UUID | None,
        limit: int,
    ) -> list[ProductionTrace]:
        """Return eval-candidate traces for ``team_id`` with id greater than ``after_id``."""
        stmt = (
            select(ProductionTrace)
            .where(
                ProductionTrace.team_id == team_id,
                ProductionTrace.is_eval_candidate.is_(True),
            )
            .order_by(ProductionTrace.id)
            .limit(limit)
        )
        if after_id is not None:
            stmt = stmt.where(ProductionTrace.id > after_id)
        return list(self.session.scalars(stmt))

    def mark_processed(self, pk: UUID) -> None:
        """Stamp ``processed_at`` on the production trace ``pk`` with the current time."""
        production_trace = self.session.get(ProductionTrace, pk)
        if production_trace is None:
            return
        production_trace.processed_at = datetime.now(UTC)
        self.session.flush()
