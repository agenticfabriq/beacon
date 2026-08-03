"""Eval-item registry primitives."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import StrEnum
from typing import Any
from uuid import UUID  # noqa: TC003

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base


class EvalItemTier(StrEnum):
    MODEL_PROPOSED = "model_proposed"
    EXECUTION_CONFIRMED = "execution_confirmed"
    HUMAN_VERIFIED = "human_verified"


class EvalItem(Base):
    """Temporally versioned eval-set member."""

    __tablename__ = "eval_items"

    item_id: Mapped[UUID] = mapped_column(primary_key=True)
    valid_from: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        primary_key=True,
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    tier: Mapped[EvalItemTier] = mapped_column(String(40), nullable=False)
    suite: Mapped[str] = mapped_column(String(200), nullable=False)
    team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=True,
    )
    solution_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dataset_version: Mapped[str] = mapped_column(String(100), nullable=False)
    item_input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    gold_answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    item_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )

    question_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "tier IN ('model_proposed','execution_confirmed','human_verified')",
            name="ck_eval_items_tier",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_eval_items_valid_to_after_from",
        ),
        Index("ix_eval_items_suite_tier", "suite", "tier"),
        Index("ix_eval_items_team", "team_id"),
        Index("ix_eval_items_question_hash", "question_hash"),
        Index(
            "ix_eval_items_active_lookup",
            "item_id",
            postgresql_where=text("valid_to IS NULL"),
        ),
    )
