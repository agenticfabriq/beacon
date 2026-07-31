"""/v1/registry/items + /v1/registry/items/{id}/promote."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

from beacon_iam.permissions import Permission, effective_permissions
from beacon_registry.errors import EvalItemNotFoundError, InvalidTierTransitionError
from beacon_registry.items import ItemService
from beacon_registry.provenance import ProvenanceService
from beacon_registry.types import ActorType, EvalItemTier
from beacon_storage.models.tenancy import ScopeKind, User  # noqa: TC002
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.projects import ProjectRepo
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_current_user, get_session
from beacon_ui.api.openapi import requires
from beacon_ui.api.schemas.registry import EvalItemListOut, EvalItemOut, PromoteIn, PromoteOut

router = APIRouter(prefix="/v1/registry", tags=["registry"])


@router.get("/items", response_model=EvalItemListOut)
def list_items(
    _user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    suite: Annotated[str | None, Query()] = None,
    tier: Annotated[EvalItemTier | None, Query()] = None,
    team_id: Annotated[UUID | None, Query()] = None,
) -> EvalItemListOut:
    """List eval items in the registry, optionally filtered by suite, tier, or team."""
    service = ItemService(session)
    if team_id is None:
        items = service.query(suite=suite, tier=tier)
    else:
        items = service.query(suite=suite, tier=tier, team_id=team_id)
    return EvalItemListOut(
        items=[EvalItemOut.model_validate(item) for item in items],
        total=len(items),
    )


def _require_promote_authority(
    *,
    session: Session,
    actor: User,
    item_id: UUID,
    project_id: UUID | None,
) -> None:
    memberships = MembershipRepo(session).list_for_user(actor.id)
    if project_id is None:
        global_permissions = effective_permissions(
            actor.id,
            memberships,
            target_scope_kind=ScopeKind.GLOBAL,
            target_scope_id=actor.id,
        )
        if Permission.GLOBAL_ADMIN in global_permissions:
            return
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "promote requires project_id with project.promote_item or global admin",
        )

    project = ProjectRepo(session).get(project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"project {project_id} not found")

    project_permissions = effective_permissions(
        actor.id,
        memberships,
        target_scope_kind=ScopeKind.PROJECT,
        target_scope_id=project_id,
        target_project_team_id=project.team_id,
    )
    if Permission.PROJECT_PROMOTE_ITEM not in project_permissions:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "missing permission: project.promote_item",
        )

    item = ItemService(session).get_active(item_id)
    if item is not None and item.team_id != project.team_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"item {item_id} not found")


@router.post(
    "/items/{item_id}/promote",
    response_model=PromoteOut,
    status_code=status.HTTP_200_OK,
)
@requires(Permission.PROJECT_PROMOTE_ITEM)
def promote_item(
    item_id: UUID,
    body: PromoteIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    project_id: Annotated[
        UUID | None,
        Query(description="Scope-of-authority project"),
    ] = None,
) -> PromoteOut:
    """Promote a registry item to a new tier and record a provenance event."""
    _require_promote_authority(
        session=session,
        actor=user,
        item_id=item_id,
        project_id=project_id,
    )
    try:
        event = ProvenanceService(session).promote_tier(
            item_id=item_id,
            new_tier=body.new_tier,
            actor_type=ActorType.HUMAN,
            actor_id=str(user.id),
            created_by=user.id,
            reason=body.reason,
            evidence=body.evidence,
        )
    except InvalidTierTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except EvalItemNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return PromoteOut(
        event_id=event.event_id,
        item_id=event.item_id,
        prior_tier=event.prior_tier,
        new_tier=event.new_tier,
        actor=user.email,
        created_at=event.created_at,
    )
