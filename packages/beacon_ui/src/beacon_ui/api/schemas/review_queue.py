"""Pydantic schemas for project review-queue APIs."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Literal
from uuid import UUID  # noqa: TC003

from beacon_registry.types import EvalItemTier  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from beacon_ui.api.schemas.registry import EvalItemOut  # noqa: TC001

ReviewAction = Literal["accept", "reject", "defer"]


class ReviewQueueOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EvalItemOut]
    total: int
    limit: int
    offset: int


class ReviewDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ReviewAction
    reason: str = Field(min_length=1, max_length=4000)
    defer_days: int | None = Field(default=None, ge=1, le=90)


class ReviewDecisionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    action: ReviewAction
    new_tier: EvalItemTier
    actor: str
    event_id: UUID | None = None
    rejected_at: datetime | None = None
    deferred_at: datetime | None = None
    deferred_until: datetime | None = None
