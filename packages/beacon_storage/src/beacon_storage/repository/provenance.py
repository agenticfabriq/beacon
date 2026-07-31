"""ProvenanceEvent repository."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from beacon_storage.models.provenance import ProvenanceEvent

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

    from beacon_storage.models.eval_items import EvalItemTier
    from beacon_storage.models.provenance import ActorType


class ProvenanceRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def append(
        self,
        *,
        item_id: UUID,
        team_id: UUID,
        prior_tier: EvalItemTier | None,
        new_tier: EvalItemTier,
        actor_type: ActorType,
        actor_id: str,
        created_by: UUID | None,
        reason: str,
        evidence: dict[str, Any],
    ) -> ProvenanceEvent:
        """Append a provenance event recording a tier transition for an eval item."""
        event = ProvenanceEvent(
            item_id=item_id,
            team_id=team_id,
            prior_tier=prior_tier,
            new_tier=new_tier,
            actor_type=actor_type,
            actor_id=actor_id,
            created_by=created_by,
            reason=reason,
            evidence=evidence,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def list_for_item(self, item_id: UUID) -> list[ProvenanceEvent]:
        """Return provenance events for ``item_id`` ordered chronologically."""
        return list(
            self.session.scalars(
                select(ProvenanceEvent)
                .where(ProvenanceEvent.item_id == item_id)
                .order_by(ProvenanceEvent.created_at, ProvenanceEvent.event_id)
            )
        )

    def list_for_team(self, team_id: UUID) -> list[ProvenanceEvent]:
        """Return provenance events for ``team_id`` ordered chronologically."""
        return list(
            self.session.scalars(
                select(ProvenanceEvent)
                .where(ProvenanceEvent.team_id == team_id)
                .order_by(ProvenanceEvent.created_at, ProvenanceEvent.event_id)
            )
        )
