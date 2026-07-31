"""Append-only ledger of eval-item tier transitions."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import StrEnum
from typing import Any
from uuid import UUID  # noqa: TC003

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.ids import uuid7
from beacon_storage.models.base import Base
from beacon_storage.models.eval_items import EvalItemTier  # noqa: TC001


class ActorType(StrEnum):
    SYSTEM = "system"
    LLM_JUDGE = "llm_judge"
    HUMAN = "human"


class ProvenanceEvent(Base):
    """Append-only event for an eval-item tier transition."""

    __tablename__ = "provenance_events"

    event_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    item_id: Mapped[UUID] = mapped_column(nullable=False)
    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )

    prior_tier: Mapped[EvalItemTier | None] = mapped_column(String(40), nullable=True)
    new_tier: Mapped[EvalItemTier] = mapped_column(String(40), nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('system','llm_judge','human')",
            name="ck_provenance_events_actor_type",
        ),
        CheckConstraint(
            "new_tier IN ('model_proposed','execution_confirmed','human_verified')",
            name="ck_provenance_events_new_tier",
        ),
        CheckConstraint(
            "prior_tier IS NULL OR prior_tier IN "
            "('model_proposed','execution_confirmed','human_verified')",
            name="ck_provenance_events_prior_tier",
        ),
        Index("ix_provenance_events_item", "item_id", "created_at"),
        Index("ix_provenance_events_team", "team_id"),
    )
