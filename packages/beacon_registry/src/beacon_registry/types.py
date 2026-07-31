"""Pydantic type surface for beacon_registry."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.provenance import ActorType
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ActorType",
    "EvalItemSummary",
    "EvalItemTier",
    "PromoteRequest",
    "TraceIngestRequest",
    "TraceIngestResult",
]


class PromoteRequest(BaseModel):
    """Manual-promotion REST payload and CLI input."""

    model_config = ConfigDict(extra="forbid")

    new_tier: EvalItemTier
    reason: str = Field(min_length=1, max_length=4000)
    evidence: dict[str, Any] = Field(default_factory=dict)


class EvalItemSummary(BaseModel):
    """Listing-page summary for registry views."""

    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    tier: EvalItemTier
    suite: str
    dataset_version: str
    team_id: UUID | None
    valid_from: str
    item_input_preview: str


class TraceIngestRequest(BaseModel):
    """SDK POST /v1/traces body."""

    model_config = ConfigDict(extra="forbid")

    solution_id: str = Field(min_length=1, max_length=200)
    project_id: UUID | None = None
    item_input: dict[str, Any]
    item_output: dict[str, Any]
    trace: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_eval_candidate: bool = False


class TraceIngestResult(BaseModel):
    """Trace ingest response returned to SDK clients."""

    model_config = ConfigDict(extra="forbid")

    production_trace_id: UUID
    derived_trace_id: UUID | None = None
    created_at: datetime
