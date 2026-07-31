"""Raw SDK-ingest log table."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from sqlalchemy import TIMESTAMP, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base, IdMixin, TimestampsMixin


class ProductionTrace(Base, IdMixin, TimestampsMixin):
    """One row per client trace payload before registry processing."""

    __tablename__ = "production_traces"

    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    solution_id: Mapped[str] = mapped_column(String(200), nullable=False)
    api_key_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    item_input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    item_output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    trace_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    trace_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    is_eval_candidate: Mapped[bool] = mapped_column(nullable=False, default=False)
    derived_trace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("traces.id", ondelete="SET NULL"),
        nullable=True,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_production_traces_team", "team_id"),
        Index("ix_production_traces_solution", "solution_id"),
        Index(
            "ix_production_traces_unprocessed",
            "created_at",
            postgresql_where=text("processed_at IS NULL"),
        ),
    )
