"""Project settings API routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

from beacon_iam.permissions import Permission
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.projects import ProjectRepo
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.project_settings import (
    ProjectSettingsOut,
    ProjectSettingsPatch,
)

router = APIRouter(prefix="/v1/projects", tags=["projects"])


@router.patch(
    "/{project_id}/settings",
    response_model=ProjectSettingsOut,
    status_code=status.HTTP_200_OK,
    summary="Update project settings",
)
@requires(Permission.PROJECT_MANAGE)
def patch_project_settings(
    project_id: UUID,
    body: ProjectSettingsPatch,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.PROJECT_MANAGE, scope_kind="project")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> ProjectSettingsOut:
    """Update the project's baseline run pin and gate policy."""
    project = ProjectRepo(session).get(project_id)
    assert project is not None

    if "baseline_run_id" in body.model_fields_set:
        project.baseline_run_id = body.baseline_run_id
    if body.gate_policy is not None:
        project.gate_policy = body.gate_policy

    session.flush()
    return ProjectSettingsOut(
        project_id=project.id,
        baseline_run_id=project.baseline_run_id,
        gate_policy=project.gate_policy or {},
    )
