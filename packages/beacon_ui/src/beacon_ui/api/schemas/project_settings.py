from __future__ import annotations

from typing import Any
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel


class ProjectSettingsPatch(BaseModel):
    baseline_run_id: UUID | None = None
    gate_policy: dict[str, Any] | None = None


class ProjectSettingsOut(BaseModel):
    project_id: UUID
    baseline_run_id: UUID | None
    gate_policy: dict[str, Any]
