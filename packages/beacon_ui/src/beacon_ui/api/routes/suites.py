"""Benchmarks: create, list, and pin the reference run."""

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID  # noqa: TC003

from beacon_iam.permissions import Permission
from beacon_registry.errors import DuplicateSuiteError
from beacon_registry.suites import SuiteService
from beacon_storage.models.suites import Suite  # noqa: TC002
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.suites import SuiteRepo
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.project_suite import (
    SuiteCreateIn,
    SuiteKind,
    SuiteMethod,
    SuiteOut,
    SuitePatchIn,
)

router = APIRouter(prefix="/v1", tags=["suites"])


def _suite_out(session: Session, suite: Suite) -> SuiteOut:
    kind: SuiteKind = "curated" if suite.method == "separability_gain" else "manual"
    method = cast("SuiteMethod", suite.method)
    # An item belongs to a benchmark two ways, and BOTH are real. Ingested
    # corpora carry the suite's NAME on the item -- the key grading and the
    # matrix use -- while a hand-built suite exists only as rows in the
    # eval_item_suites join table, its items keeping whatever name they came
    # with. Counting the join table alone read "10 questions" for
    # bird_minidev_v2, whose 10 rows are a leftover demo slice, directly beside
    # a table reporting n=487 of the same benchmark. Counting by name alone
    # gives a hand-built suite zero. The union is the only count that is right
    # for both, and it is a union of IDS so an item claimed both ways is still
    # one question.
    by_name = {
        item.item_id
        for item in EvalItemRepo(session).list_active(suite=suite.name, team_id=suite.team_id)
    }
    item_count = len(by_name | set(SuiteRepo(session).list_item_ids(suite.id)))
    return SuiteOut(
        suite_id=suite.id,
        id=suite.id,
        team_id=suite.team_id,
        baseline_run_id=suite.baseline_run_id,
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
    "/teams/{team_id}/suites",
    response_model=SuiteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a suite",
)
@requires(Permission.EVAL_MANAGE)
def create_suite(
    team_id: UUID,
    body: SuiteCreateIn,
    user: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_MANAGE, scope_kind="team")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> SuiteOut:
    """Create a benchmark owned by the team."""
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
            team_id=team_id,
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

    return _suite_out(session, suite)


@router.get("/teams/{team_id}/suites", response_model=list[SuiteOut])
@requires(Permission.EVAL_VIEW)
def list_suites(
    team_id: UUID,
    _user: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_VIEW, scope_kind="team")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> list[SuiteOut]:
    """List the team's benchmarks."""
    suites = SuiteRepo(session).list_for_team(team_id)
    return [_suite_out(session, suite) for suite in suites]


@router.patch(
    "/suites/{suite_id}",
    response_model=SuiteOut,
    summary="Update a benchmark, e.g. pin its reference run",
)
@requires(Permission.EVAL_MANAGE)
def patch_suite(
    suite_id: UUID,
    body: SuitePatchIn,
    _user: Annotated[
        User,
        Depends(require_permission(Permission.EVAL_MANAGE, scope_kind="suite")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> SuiteOut:
    """Pin or clear the reference run every other run is read against."""
    repo = SuiteRepo(session)
    suite = repo.get(suite_id)
    assert suite is not None  # the permission dependency 404s first
    if "baseline_run_id" in body.model_fields_set:
        repo.set_baseline(suite_id, body.baseline_run_id)
    session.commit()
    return _suite_out(session, suite)
