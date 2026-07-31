from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel


class ProjectSolutionLink(BaseModel):
    solution_id: UUID


class ProjectSolutionOut(BaseModel):
    project_id: UUID
    solution_id: UUID
    solution_name: str
    solution_version: str
    added_at: datetime
