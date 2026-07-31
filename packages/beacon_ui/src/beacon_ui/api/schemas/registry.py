"""Pydantic schemas for /v1/registry/* endpoints."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from beacon_registry.types import EvalItemTier  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field


class EvalItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    item_id: UUID
    valid_from: datetime
    valid_to: datetime | None
    tier: EvalItemTier
    suite: str
    team_id: UUID | None
    solution_id: str | None
    dataset_version: str
    item_input: dict[str, Any]
    gold_answer: dict[str, Any] | None
    metadata: dict[str, Any] = Field(
        validation_alias="item_metadata",
        default_factory=dict,
    )


class EvalItemListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EvalItemOut]
    total: int


class PromoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_tier: EvalItemTier
    reason: str = Field(min_length=1, max_length=4000)
    evidence: dict[str, Any] = Field(default_factory=dict)


class PromoteOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    item_id: UUID
    prior_tier: EvalItemTier | None
    new_tier: EvalItemTier
    actor: str
    created_at: datetime
