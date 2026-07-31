"""Project solution link API routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

from beacon_iam.permissions import Permission
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.project_solutions import ProjectSolutionRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.solutions import SolutionRepo
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.project_solution import ProjectSolutionLink, ProjectSolutionOut

router = APIRouter(prefix="/v1/projects", tags=["projects"])


@router.post(
    "/{project_id}/solutions",
    response_model=ProjectSolutionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Attach a team-catalog solution to this project",
)
@requires(Permission.PROJECT_MANAGE)
def add_project_solution(
    project_id: UUID,
    body: ProjectSolutionLink,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.PROJECT_MANAGE, scope_kind="project")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> ProjectSolutionOut:
    """Attach a team-catalog solution to the given project."""
    project = ProjectRepo(session).get(project_id)
    assert project is not None

    solution = SolutionRepo(session).get(body.solution_id)
    if solution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"solution {body.solution_id} not found")
    if solution.team_id != project.team_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "solution belongs to a different team than this project",
        )

    link = ProjectSolutionRepo(session).link(
        team_id=project.team_id,
        project_id=project_id,
        solution_id=body.solution_id,
    )
    return ProjectSolutionOut(
        project_id=project_id,
        solution_id=solution.id,
        solution_name=solution.solution_id,
        solution_version=solution.version,
        added_at=link.added_at,
    )


@router.delete(
    "/{project_id}/solutions/{solution_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Detach a solution from this project",
)
@requires(Permission.PROJECT_MANAGE)
def remove_project_solution(
    project_id: UUID,
    solution_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.PROJECT_MANAGE, scope_kind="project")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> None:
    """Detach a solution from the given project."""
    ProjectSolutionRepo(session).unlink(project_id=project_id, solution_id=solution_id)


@router.get(
    "/{project_id}/solutions",
    response_model=list[ProjectSolutionOut],
    summary="List solutions attached to this project",
)
@requires(Permission.PROJECT_VIEW)
def list_project_solutions(
    project_id: UUID,
    _actor: Annotated[
        User,
        Depends(require_permission(Permission.PROJECT_VIEW, scope_kind="project")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> list[ProjectSolutionOut]:
    """List solutions attached to the given project."""
    return [
        ProjectSolutionOut(
            project_id=project_id,
            solution_id=solution.id,
            solution_name=solution.solution_id,
            solution_version=solution.version,
            added_at=added_at,
        )
        for solution, added_at in ProjectSolutionRepo(session).list_with_solution(project_id)
    ]
