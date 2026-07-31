"""GET /v1/teams/{team_id}/anti-goodhart."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Annotated, Any
from uuid import UUID  # noqa: TC003

from beacon_iam.permissions import Permission
from beacon_storage.models.antigoodhart import (
    AntigoodhartFinding,
    AntigoodhartKind,
    AntigoodhartSeverity,
)
from beacon_storage.models.tenancy import Role, ScopeKind, User  # noqa: TC002
from beacon_storage.repository.memberships import MembershipRepo
from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, desc, select
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_current_user, get_session
from beacon_ui.api.openapi import requires

router = APIRouter(prefix="/v1/teams", tags=["anti-goodhart"])


@router.get("/{team_id}/anti-goodhart")
@requires(Permission.TEAM_VIEW)
def list_findings(
    team_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    since: Annotated[datetime | None, Query()] = None,
    kind: Annotated[AntigoodhartKind | None, Query()] = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return recent anti-Goodhart findings for the given team."""
    if not _can_view_team(session=session, user_id=user.id, team_id=team_id):
        return {"findings": []}

    severity_rank = case(
        (AntigoodhartFinding.severity == AntigoodhartSeverity.HIGH, 0),
        (AntigoodhartFinding.severity == AntigoodhartSeverity.MEDIUM, 1),
        else_=2,
    )
    stmt = (
        select(AntigoodhartFinding)
        .where(AntigoodhartFinding.team_id == team_id)
        .order_by(
            severity_rank,
            desc(AntigoodhartFinding.created_at),
            desc(AntigoodhartFinding.id),
        )
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(AntigoodhartFinding.created_at >= since)
    if kind is not None:
        stmt = stmt.where(AntigoodhartFinding.kind == kind)

    rows = session.scalars(stmt).all()
    return {"findings": [_finding_out(row) for row in rows]}


def _can_view_team(*, session: Session, user_id: UUID, team_id: UUID) -> bool:
    memberships = MembershipRepo(session).list_for_user(user_id)
    return any(
        membership.scope_kind == ScopeKind.GLOBAL
        and membership.role == Role.BEACON_ADMIN
        or membership.scope_kind == ScopeKind.TEAM
        and membership.scope_id == team_id
        for membership in memberships
    )


def _finding_out(row: AntigoodhartFinding) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": _stored_value(row.kind),
        "severity": _stored_value(row.severity),
        "eval_item_id": str(row.item_id) if row.item_id is not None else None,
        "suite_id": str(row.suite_id) if row.suite_id is not None else None,
        "detail": row.description,
        "created_at": row.created_at.isoformat(),
        "created_at_ts": int(row.created_at.timestamp()),
    }


def _stored_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)
