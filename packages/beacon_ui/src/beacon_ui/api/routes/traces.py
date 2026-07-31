"""POST /v1/traces SDK ingestion endpoint."""

from __future__ import annotations

from typing import Annotated

from beacon_registry.ingestion import TraceIngestService
from beacon_registry.types import TraceIngestRequest
from beacon_storage.models.tenancy import ScopeKind, User  # noqa: TC002
from beacon_storage.repository.memberships import MembershipRepo
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_current_user, get_session
from beacon_ui.api.schemas.trace import TraceCreateIn, TraceCreateOut

router = APIRouter(prefix="/v1", tags=["traces"])


@router.post(
    "/traces",
    response_model=TraceCreateOut,
    status_code=status.HTTP_201_CREATED,
)
def create_trace(
    body: TraceCreateIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> TraceCreateOut:
    """Ingest a production trace and optionally derive an eval candidate."""
    memberships = MembershipRepo(session).list_for_user(user.id)
    team_memberships = [
        membership for membership in memberships if membership.scope_kind == ScopeKind.TEAM
    ]
    if not team_memberships:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="caller has no team membership; cannot ingest traces",
        )

    request = TraceIngestRequest(
        solution_id=body.solution_id,
        project_id=body.project_id,
        item_input=body.item_input,
        item_output=body.item_output,
        trace=body.trace,
        metadata=body.metadata,
        is_eval_candidate=body.is_eval_candidate,
    )
    result = TraceIngestService(session).ingest(
        request=request,
        team_id=team_memberships[0].scope_id,
        api_key_id=None,
    )
    return TraceCreateOut(
        production_trace_id=result.production_trace_id,
        derived_trace_id=result.derived_trace_id,
        created_at=result.created_at,
    )
