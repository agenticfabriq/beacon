from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field


class ProjectIn(BaseModel):
    name: str
    description: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    team_id: UUID
    name: str
    description: str | None
    baseline_run_id: UUID | None = None
    gate_policy: dict[str, Any] = Field(default_factory=dict)
    created_by: UUID
    created_at: datetime
    archived_at: datetime | None


class ProjectMemberIn(BaseModel):
    user_id: UUID
    role: str
