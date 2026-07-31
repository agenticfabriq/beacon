"""Human review queue helpers for execution-confirmed eval items."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from beacon_storage.models.eval_items import EvalItem
from beacon_storage.models.provenance import ProvenanceEvent
from beacon_storage.models.tenancy import Project
from sqlalchemy import Select, false, func, or_, select

from beacon_registry.errors import EvalItemNotFoundError
from beacon_registry.types import ActorType, EvalItemTier

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class ReviewQueueService:
    """List and mutate human-review queue state."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_pending(
        self,
        *,
        project_id: UUID,
        suite: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EvalItem]:
        """Return active execution-confirmed items that have not been rejected."""
        stmt = self._pending_stmt(project_id=project_id, suite=suite).order_by(
            EvalItem.deferred_at.asc().nullsfirst(),
            EvalItem.created_at.asc(),
        )
        return list(self.session.scalars(stmt.limit(limit).offset(offset)))

    def count_pending(
        self,
        *,
        project_id: UUID,
        suite: str | None = None,
    ) -> int:
        """Count active execution-confirmed items that have not been rejected."""
        stmt = self._pending_stmt(project_id=project_id, suite=suite).with_only_columns(
            func.count()
        )
        return self.session.scalar(stmt) or 0

    def _pending_stmt(self, *, project_id: UUID, suite: str | None) -> Select[tuple[EvalItem]]:
        project = self.session.get(Project, project_id)
        stmt = (
            select(EvalItem)
            .where(EvalItem.valid_to.is_(None))
            .where(EvalItem.tier == EvalItemTier.EXECUTION_CONFIRMED)
            .where(EvalItem.rejected_at.is_(None))
        )
        if project is None:
            return stmt.where(false())
        stmt = stmt.where(or_(EvalItem.team_id == project.team_id, EvalItem.team_id.is_(None)))
        if suite is not None:
            stmt = stmt.where(EvalItem.suite == suite)
        return stmt

    def mark_rejected(self, *, item_id: UUID, reason: str, actor_id: UUID) -> EvalItem:
        """Exclude an active item from the queue and write an audit event."""
        item = self._get_active(item_id)
        now = datetime.now(UTC)
        item.rejected_at = now
        self.session.add(
            ProvenanceEvent(
                item_id=item_id,
                team_id=cast("UUID", item.team_id),
                prior_tier=item.tier,
                new_tier=item.tier,
                actor_type=ActorType.HUMAN,
                actor_id=str(actor_id),
                created_by=actor_id,
                reason=f"rejected: {reason}",
                evidence={"action": "rejected"},
                created_at=now,
            )
        )
        self.session.flush()
        return item

    def defer(
        self,
        *,
        item_id: UUID,
        actor_id: UUID,
        reason: str = "",
        until: datetime | None = None,
    ) -> EvalItem:
        """Push an active item behind undeferred items and write an audit event."""
        item = self._get_active(item_id)
        now = datetime.now(UTC)
        item.deferred_at = now
        evidence = {"action": "deferred"}
        if until is not None:
            evidence["deferred_until"] = until.isoformat()
        self.session.add(
            ProvenanceEvent(
                item_id=item_id,
                team_id=cast("UUID", item.team_id),
                prior_tier=item.tier,
                new_tier=item.tier,
                actor_type=ActorType.HUMAN,
                actor_id=str(actor_id),
                created_by=actor_id,
                reason=f"deferred: {reason}" if reason else "deferred",
                evidence=evidence,
                created_at=now,
            )
        )
        self.session.flush()
        return item

    def _get_active(self, item_id: UUID) -> EvalItem:
        item = self.session.scalar(
            select(EvalItem).where(
                EvalItem.item_id == item_id,
                EvalItem.valid_to.is_(None),
            )
        )
        if item is None:
            raise EvalItemNotFoundError(f"no active version for item_id {item_id}")
        return item
