"""Provenance tier-transition validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from beacon_storage.repository.provenance import ProvenanceRepo

from beacon_registry.errors import EvalItemNotFoundError, InvalidTierTransitionError
from beacon_registry.items import ItemService
from beacon_registry.types import EvalItemTier

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_storage.models.provenance import ProvenanceEvent
    from sqlalchemy.orm import Session

    from beacon_registry.types import ActorType

_ALLOWED: set[tuple[str | None, str]] = {
    (None, EvalItemTier.MODEL_PROPOSED.value),
    (EvalItemTier.MODEL_PROPOSED.value, EvalItemTier.EXECUTION_CONFIRMED.value),
    (EvalItemTier.EXECUTION_CONFIRMED.value, EvalItemTier.HUMAN_VERIFIED.value),
}


def _tier_label(tier: EvalItemTier | str | None) -> str | None:
    if tier is None:
        return None
    if isinstance(tier, EvalItemTier):
        return tier.value
    return tier


def validate_transition(*, prior: EvalItemTier | str | None, new: EvalItemTier) -> None:
    """Raise InvalidTierTransitionError if the tier transition is not allowed."""
    prior_label = _tier_label(prior)
    new_label = new.value
    if (prior_label, new_label) in _ALLOWED:
        return

    prior_message = prior_label if prior_label is not None else "<initial>"
    raise InvalidTierTransitionError(
        f"tier transition '{prior_message}' -> '{new_label}' is not allowed; "
        "valid transitions are: <initial>->model_proposed, "
        "model_proposed->execution_confirmed, "
        "execution_confirmed->human_verified"
    )


class ProvenanceService:
    """Coordinate eval-item tier transitions and provenance events."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.items = ItemService(session)
        self.events = ProvenanceRepo(session)

    def promote_tier(
        self,
        *,
        item_id: UUID,
        new_tier: EvalItemTier,
        actor_type: ActorType,
        actor_id: str,
        created_by: UUID | None,
        reason: str,
        evidence: dict[str, Any],
    ) -> ProvenanceEvent:
        """Write a new item version and provenance event in the current session."""
        current = self.items.get_active(item_id)
        if current is None:
            raise EvalItemNotFoundError(f"no active version for item_id {item_id}")

        validate_transition(prior=current.tier, new=new_tier)

        self.items.update_item(
            item_id=item_id,
            tier=new_tier,
            suite=current.suite,
            team_id=current.team_id,
            solution_id=current.solution_id,
            dataset_version=current.dataset_version,
            item_input=current.item_input,
            gold_answer=current.gold_answer,
            item_metadata=current.item_metadata,
            created_by=created_by,
        )

        return self.events.append(
            item_id=item_id,
            team_id=cast("UUID", current.team_id),
            prior_tier=current.tier,
            new_tier=new_tier,
            actor_type=actor_type,
            actor_id=actor_id,
            created_by=created_by,
            reason=reason,
            evidence=evidence,
        )
