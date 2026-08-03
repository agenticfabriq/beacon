"""Suite registry models."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base, IdMixin, TimestampsMixin


class Suite(Base, IdMixin, TimestampsMixin):
    """A benchmark: a team-owned named set of eval items runs are scored over.

    Carries the reference run every other run is read against. It lived on the
    Project before; a reference is per benchmark, and the project level is gone.
    """

    __tablename__ = "suites"

    # No FK: a run references its suite, so the reference run would be a cycle.
    # Cleared by run invalidation at the service layer.
    baseline_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    method: Mapped[str] = mapped_column(String(60), nullable=False)
    suite_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "method IN ('manual','separability_gain','handpicked')",
            name="ck_suites_method",
        ),
        Index("ix_suites_team", "team_id"),
        UniqueConstraint("team_id", "name", name="uq_suites_team_name"),
    )


class EvalItemSuite(Base):
    """Join table from suites to active eval item identities."""

    __tablename__ = "eval_item_suites"

    suite_id: Mapped[UUID] = mapped_column(
        ForeignKey("suites.id", ondelete="CASCADE"),
        primary_key=True,
    )
    item_id: Mapped[UUID] = mapped_column(primary_key=True)
    added_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (Index("ix_eval_item_suites_item", "item_id"),)
