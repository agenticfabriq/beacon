"""Per-worker watermark and operational state."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from sqlalchemy import TIMESTAMP, BigInteger, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base


class WorkerState(Base):
    """Watermark row for one worker and optional team scope."""

    __tablename__ = "worker_state"

    worker_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[UUID | None] = mapped_column(primary_key=True, nullable=True)

    last_processed_id: Mapped[UUID | None] = mapped_column(nullable=True)
    last_processed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    tick_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    error_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    last_error_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "uq_worker_state_worker_team",
            "worker_name",
            "team_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_worker_state_updated_at", "updated_at"),
    )
