from __future__ import annotations

from uuid import UUID  # noqa: TC003

from pydantic import BaseModel


class ProjectSettingsPatch(BaseModel):
    baseline_run_id: UUID | None = None


class ProjectSettingsOut(BaseModel):
    project_id: UUID
    baseline_run_id: UUID | None
