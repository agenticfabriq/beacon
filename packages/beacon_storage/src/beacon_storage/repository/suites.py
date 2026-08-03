"""Suite and EvalItemSuite repository."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from beacon_storage.models.suites import EvalItemSuite, Suite

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


class SuiteRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        team_id: UUID,
        name: str,
        description: str,
        method: str,
        suite_metadata: dict[str, Any],
        created_by: UUID,
    ) -> Suite:
        """Persist a new suite under ``team_id`` and return it."""
        suite = Suite(
            team_id=team_id,
            name=name,
            description=description,
            method=method,
            suite_metadata=suite_metadata,
            created_by=created_by,
        )
        self.session.add(suite)
        self.session.flush()
        return suite

    def set_baseline(self, suite_id: UUID, run_id: UUID | None) -> None:
        """Pin (or clear) the reference run every other run is read against."""
        suite = self.get(suite_id)
        if suite is not None:
            suite.baseline_run_id = run_id
            self.session.flush()

    def get(self, suite_id: UUID) -> Suite | None:
        """Return the suite with id ``suite_id`` or None."""
        return self.session.get(Suite, suite_id)

    def get_by_team_and_name(self, team_id: UUID, name: str) -> Suite | None:
        """Return the suite identified by ``(team_id, name)`` or None."""
        return self.session.scalar(
            select(Suite).where(
                Suite.team_id == team_id,
                Suite.name == name,
            )
        )

    def list_for_team(self, team_id: UUID) -> list[Suite]:
        """Return suites owned by ``team_id`` ordered by name."""
        return list(
            self.session.scalars(select(Suite).where(Suite.team_id == team_id).order_by(Suite.name))
        )

    def add_items(self, *, suite_id: UUID, item_ids: list[UUID]) -> int:
        """Insert ``item_ids`` into ``suite_id`` ignoring duplicates; return count added."""
        if not item_ids:
            return 0
        stmt = (
            pg_insert(EvalItemSuite)
            .values([{"suite_id": suite_id, "item_id": item_id} for item_id in item_ids])
            .on_conflict_do_nothing(index_elements=["suite_id", "item_id"])
            .returning(EvalItemSuite.item_id)
        )
        inserted = list(self.session.scalars(stmt))
        self.session.flush()
        return len(inserted)

    def remove_items(self, *, suite_id: UUID, item_ids: list[UUID]) -> int:
        """Remove ``item_ids`` from ``suite_id`` and return count actually deleted."""
        if not item_ids:
            return 0
        result = self.session.execute(
            delete(EvalItemSuite)
            .where(
                EvalItemSuite.suite_id == suite_id,
                EvalItemSuite.item_id.in_(item_ids),
            )
            .returning(EvalItemSuite.item_id)
        )
        deleted = list(result.scalars())
        self.session.flush()
        return len(deleted)

    def list_item_ids(self, suite_id: UUID) -> list[UUID]:
        """Return the eval-item ids that belong to ``suite_id`` in ascending order."""
        return list(
            self.session.scalars(
                select(EvalItemSuite.item_id)
                .where(EvalItemSuite.suite_id == suite_id)
                .order_by(EvalItemSuite.item_id)
            )
        )
