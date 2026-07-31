"""ItemService append-only versioning for eval items."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from beacon_storage.ids import uuid7
from beacon_storage.repository.eval_items import EvalItemRepo

from beacon_registry.errors import EvalItemNotFoundError

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_storage.models.eval_items import EvalItem
    from sqlalchemy.orm import Session

    from beacon_registry.types import EvalItemTier


_UNSET: Any = object()


def _now() -> datetime:
    return datetime.now(UTC)


class ItemService:
    """Service boundary for creating and versioning eval items."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = EvalItemRepo(session)

    def create_item(
        self,
        *,
        tier: EvalItemTier,
        suite: str,
        team_id: UUID | None,
        solution_id: str | None,
        dataset_version: str,
        item_input: dict[str, Any],
        gold_answer: dict[str, Any] | None,
        item_metadata: dict[str, Any],
        created_by: UUID | None,
    ) -> UUID:
        """Insert the first version of a new eval item and return its id."""
        item_id = uuid7()
        self.repo.insert_new_version(
            item_id=item_id,
            valid_from=_now(),
            tier=tier,
            suite=suite,
            team_id=team_id,
            solution_id=solution_id,
            dataset_version=dataset_version,
            item_input=item_input,
            gold_answer=gold_answer,
            item_metadata=item_metadata,
            created_by=created_by,
        )
        return item_id

    def update_item(
        self,
        *,
        item_id: UUID,
        tier: EvalItemTier,
        suite: str,
        team_id: UUID | None,
        solution_id: str | None,
        dataset_version: str,
        item_input: dict[str, Any],
        gold_answer: dict[str, Any] | None,
        item_metadata: dict[str, Any],
        created_by: UUID | None,
    ) -> EvalItem:
        """Close the current version and append a new active version of the item."""
        current = self.repo.get_active(item_id)
        if current is None:
            raise EvalItemNotFoundError(f"no active version for item_id {item_id}; cannot update")

        new_valid_from = _now()
        if new_valid_from <= current.valid_from:
            new_valid_from = current.valid_from + timedelta(microseconds=1)

        self.repo.set_valid_to(
            item_id=item_id,
            valid_from=current.valid_from,
            valid_to=new_valid_from,
        )
        return self.repo.insert_new_version(
            item_id=item_id,
            valid_from=new_valid_from,
            tier=tier,
            suite=suite,
            team_id=team_id,
            solution_id=solution_id,
            dataset_version=dataset_version,
            item_input=item_input,
            gold_answer=gold_answer,
            item_metadata=item_metadata,
            created_by=created_by,
        )

    def get_active(self, item_id: UUID) -> EvalItem | None:
        """Return the currently active version of the eval item, if any."""
        return self.repo.get_active(item_id)

    def get_version(self, item_id: UUID, valid_from: datetime) -> EvalItem | None:
        """Return the eval item version with the given valid_from timestamp."""
        return self.repo.get_version(item_id, valid_from)

    def list_history(self, item_id: UUID) -> list[EvalItem]:
        """List all versions of the eval item ordered by valid_from."""
        return self.repo.list_history(item_id)

    def query(
        self,
        *,
        suite: str | None = None,
        tier: EvalItemTier | None = None,
        team_id: UUID | None = _UNSET,
    ) -> list[EvalItem]:
        """List active eval items filtered by suite, tier, and optionally team."""
        if team_id is _UNSET:
            return self.repo.list_active(suite=suite, tier=tier)
        return self.repo.list_active(suite=suite, tier=tier, team_id=team_id)
