"""POST + GET /v1/projects/{project_id}/suites."""

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID  # noqa: TC003

from beacon_iam.permissions import Permission
from beacon_registry.errors import DuplicateSuiteError
from beacon_registry.suites import SuiteService
from beacon_storage.models.suites import Suite  # noqa: TC002
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.projects import ProjectRepo
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.project_suite import SuiteCreateIn, SuiteKind, SuiteMethod, SuiteOut

router = APIRouter(prefix="/v1/projects", tags=["suites"])


def _suite_out(service: SuiteService, suite: Suite) -> SuiteOut:
    kind: SuiteKind = "curated" if suite.method == "separability_gain" else "manual"
    method = cast("SuiteMethod", suite.method)
    item_count = len(service.list_item_ids(suite.id))
    return SuiteOut(
        suite_id=suite.id,
        id=suite.id,
        project_id=suite.project_id,
        team_id=suite.team_id,
        name=suite.name,
        description=suite.description,
        method=method,
        kind=kind,
        metadata=suite.suite_metadata,
        created_by=suite.created_by,
        created_at=suite.created_at,
        item_count=item_count,
    )


@router.post(
    "/{project_id}/suites",
    response_model=SuiteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a suite",
)
@requires(Permission.PROJECT_MANAGE)
def create_suite(
    project_id: UUID,
    body: SuiteCreateIn,
    user: Annotated[
        User,
        Depends(require_permission(Permission.PROJECT_MANAGE, scope_kind="project")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> SuiteOut:
    """Create a curated or manual suite within a project."""
    project = ProjectRepo(session).get(project_id)
    assert project is not None

    metadata = dict(body.metadata)
    if body.resolved_kind == "curated":
        metadata.update(
            {
                "source_suite": body.source_suite,
                "target_size": body.target_size or 50,
            }
        )

    try:
        service = SuiteService(session)
        suite = service.create(
            project_id=project_id,
            team_id=project.team_id,
            name=body.name,
            description=body.description,
            method=body.resolved_method,
            suite_metadata=metadata,
            created_by=user.id,
        )
        if body.resolved_kind == "manual":
            service.add_items(suite_id=suite.id, item_ids=body.item_ids)
    except DuplicateSuiteError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return _suite_out(service, suite)


@router.get("/{project_id}/suites", response_model=list[SuiteOut])
@requires(Permission.PROJECT_VIEW)
def list_suites(
    project_id: UUID,
    _user: Annotated[
        User,
        Depends(require_permission(Permission.PROJECT_VIEW, scope_kind="project")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> list[SuiteOut]:
    """List suites defined in the given project."""
    project = ProjectRepo(session).get(project_id)
    assert project is not None

    service = SuiteService(session)
    suites = service.list_for_project(project_id)
    return [_suite_out(service, suite) for suite in suites]
