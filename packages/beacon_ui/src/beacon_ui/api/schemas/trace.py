"""Pydantic schemas for /v1/traces."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field


class TraceCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solution_id: str = Field(min_length=1, max_length=200)
    project_id: UUID | None = None
    item_input: dict[str, Any]
    item_output: dict[str, Any]
    trace: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_eval_candidate: bool = False


class TraceCreateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    production_trace_id: UUID
    derived_trace_id: UUID | None
    created_at: datetime
