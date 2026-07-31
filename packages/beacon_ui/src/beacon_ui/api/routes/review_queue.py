"""Project review-queue API routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003

from beacon_iam.permissions import Permission
from beacon_registry.errors import EvalItemNotFoundError, InvalidTierTransitionError
from beacon_registry.items import ItemService
from beacon_registry.provenance import ProvenanceService
from beacon_registry.review_queue import ReviewQueueService
from beacon_registry.types import ActorType, EvalItemTier
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.provenance import ProvenanceRepo
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_session, require_permission
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.registry import EvalItemOut
from beacon_ui.api.schemas.review_queue import (
    ReviewDecisionIn,
    ReviewDecisionOut,
    ReviewQueueOut,
)

if TYPE_CHECKING:
    from beacon_storage.models.eval_items import EvalItem

router = APIRouter(prefix="/v1/projects", tags=["review-queue"])


def _reviewable_item(
    session: Session,
    *,
    project_id: UUID,
    item_id: UUID,
) -> EvalItem:
    project = ProjectRepo(session).get(project_id)
    assert project is not None

    item = ItemService(session).get_active(item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"item {item_id} not found")
    if item.team_id != project.team_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"item {item_id} not found")
    if item.tier != EvalItemTier.EXECUTION_CONFIRMED or item.rejected_at is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "item is not pending human review",
        )
    return item


def _latest_event_id(session: Session, item_id: UUID) -> UUID | None:
    events = ProvenanceRepo(session).list_for_item(item_id)
    if not events:
        return None
    return events[-1].event_id


@router.get("/{project_id}/review-queue", response_model=ReviewQueueOut)
@requires(Permission.PROJECT_VIEW)
def list_review_queue(
    project_id: UUID,
    _user: Annotated[
        User,
        Depends(require_permission(Permission.PROJECT_VIEW, scope_kind="project")),
    ],
    session: Annotated[Session, Depends(get_session)],
    suite: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReviewQueueOut:
    """Return pending review-queue items for the project."""
    project = ProjectRepo(session).get(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project not found",
        )

    service = ReviewQueueService(session)
    items = service.list_pending(
        project_id=project_id,
        suite=suite,
        limit=limit,
        offset=offset,
    )
    total = service.count_pending(project_id=project_id, suite=suite)
    return ReviewQueueOut(
        items=[EvalItemOut.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{project_id}/review-queue/{item_id}/decide",
    response_model=ReviewDecisionOut,
    status_code=status.HTTP_200_OK,
    summary="Accept, reject, or defer a pending review item",
)
@requires(Permission.PROJECT_PROMOTE_ITEM)
def decide_review_item(
    project_id: UUID,
    item_id: UUID,
    body: ReviewDecisionIn,
    actor: Annotated[
        User,
        Depends(require_permission(Permission.PROJECT_PROMOTE_ITEM, scope_kind="project")),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> ReviewDecisionOut:
    """Accept, reject, or defer a pending review-queue item."""
    _reviewable_item(session, project_id=project_id, item_id=item_id)

    try:
        if body.action == "accept":
            event = ProvenanceService(session).promote_tier(
                item_id=item_id,
                new_tier=EvalItemTier.HUMAN_VERIFIED,
                actor_type=ActorType.HUMAN,
                actor_id=str(actor.id),
                created_by=actor.id,
                reason=body.reason,
                evidence={"action": "accepted"},
            )
            return ReviewDecisionOut(
                item_id=item_id,
                action=body.action,
                new_tier=event.new_tier,
                actor=actor.email,
                event_id=event.event_id,
            )

        if body.action == "reject":
            rejected = ReviewQueueService(session).mark_rejected(
                item_id=item_id,
                reason=body.reason,
                actor_id=actor.id,
            )
            return ReviewDecisionOut(
                item_id=item_id,
                action=body.action,
                new_tier=rejected.tier,
                actor=actor.email,
                event_id=_latest_event_id(session, item_id),
                rejected_at=rejected.rejected_at,
            )

        deferred_until = datetime.now(UTC) + timedelta(days=body.defer_days or 1)
        deferred = ReviewQueueService(session).defer(
            item_id=item_id,
            actor_id=actor.id,
            reason=body.reason,
            until=deferred_until,
        )
        return ReviewDecisionOut(
            item_id=item_id,
            action=body.action,
            new_tier=deferred.tier,
            actor=actor.email,
            event_id=_latest_event_id(session, item_id),
            deferred_at=deferred.deferred_at,
            deferred_until=deferred_until,
        )
    except InvalidTierTransitionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except EvalItemNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
