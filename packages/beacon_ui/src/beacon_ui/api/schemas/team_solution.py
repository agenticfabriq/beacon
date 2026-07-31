"""Pydantic schemas for team solution catalog APIs."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field


class TeamSolutionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solution_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    summary: str = ""
    supported_modes: list[str] = Field(default_factory=list)
    layers: list[dict[str, Any]] = Field(default_factory=list)


class TeamSolutionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    solution_id: str
    name: str
    version: str
    owner_team: UUID
    summary: str
    supported_modes: list[str]
    layers: list[dict[str, Any]]
    created_by: UUID
    created_at: datetime
