from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID  # noqa: TC003

from beacon_iam.permissions import Permission
from beacon_iam.service.memberships import MembershipService
from beacon_iam.service.projects import ProjectService
from beacon_storage.models.tenancy import Role, User  # noqa: TC002
from beacon_storage.repository.projects import ProjectRepo
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_current_user, get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.project import ProjectIn, ProjectMemberIn, ProjectOut

router = APIRouter(prefix="/v1/projects", tags=["projects"])


@router.post(
    "",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    description="Required roles: beacon_admin, team_admin, or team_member on the target team.",
    openapi_extra={"x-required-roles": ["beacon_admin", "team_admin", "team_member"]},
)
def create_project(
    team_id: UUID,
    body: ProjectIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> ProjectOut:
    """Create a new project under the given team."""
    project = ProjectService(session).create(
        actor_id=user.id,
        team_id=team_id,
        name=body.name,
        description=body.description,
    )
    return ProjectOut.model_validate(project)


@router.get(
    "",
    response_model=list[ProjectOut],
    description="Required roles: beacon_admin, viewer, team_admin, or team_member.",
    openapi_extra={"x-required-roles": ["beacon_admin", "viewer", "team_admin", "team_member"]},
)
def list_projects(
    team_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> list[ProjectOut]:
    """List projects in a team that the caller can see."""
    projects = ProjectService(session).list_for_team(actor_id=user.id, team_id=team_id)
    return [ProjectOut.model_validate(project) for project in projects]


@router.post(
    "/{project_id}/members",
    status_code=status.HTTP_201_CREATED,
    description="Required roles: beacon_admin, team_admin, or project_owner.",
    openapi_extra={"x-required-roles": ["beacon_admin", "team_admin", "project_owner"]},
)
def add_member(
    project_id: UUID,
    body: ProjectMemberIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, str]:
    """Grant a user a role on the given project."""
    MembershipService(session).grant_project_membership(
        actor_id=user.id,
        project_id=project_id,
        target_user_id=body.user_id,
        role=Role(body.role),
    )
    return {"status": "granted"}


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a project",
)
@requires(Permission.PROJECT_MANAGE)
def archive_project(
    project_id: UUID,
    _user: Annotated[
        User,
        Depends(require_permission(Permission.PROJECT_MANAGE, scope_kind="project")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> None:
    """Mark the project as archived."""
    project = ProjectRepo(session).get(project_id)
    assert project is not None
    project.archived_at = datetime.now(UTC)
    session.flush()
