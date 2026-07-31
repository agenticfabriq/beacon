"""Pydantic schemas for PR_GATE webhook APIs."""

from __future__ import annotations

from typing import Literal
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field


class GateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solution_id: UUID
    commit_sha: str = Field(min_length=4, max_length=64)
    ci_run_url: str | None = None


class GateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["pass", "fail", "warn"]
    run_id: UUID
    baseline_run_id: UUID | None
    delta: dict[str, float]
    reason: str
