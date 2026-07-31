"""EvalItem repository with active-row semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select

from beacon_storage.ids import uuid7
from beacon_storage.models.eval_items import EvalItem, EvalItemTier
from beacon_storage.models.suites import EvalItemSuite

if TYPE_CHECKING:
    from collections.abc import Iterator
    from uuid import UUID

    from sqlalchemy.orm import Session


_UNSET: Any = object()


class EvalItemRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def insert_new_version(
        self,
        *,
        item_id: UUID,
        valid_from: datetime,
        tier: EvalItemTier,
        suite: str,
        team_id: UUID | None,
        solution_id: str | None,
        dataset_version: str,
        item_input: dict[str, Any],
        gold_answer: dict[str, Any] | None,
        item_metadata: dict[str, Any],
        question_hash: str | None = None,
        embedding: list[float] | None = None,
        evidence: str | None = None,
        created_by: UUID | None = None,
    ) -> EvalItem:
        """Insert a new (open-ended) version row for ``item_id`` and return it."""
        item = EvalItem(
            item_id=item_id,
            valid_from=valid_from,
            valid_to=None,
            tier=tier,
            suite=suite,
            team_id=team_id,
            solution_id=solution_id,
            dataset_version=dataset_version,
            item_input=item_input,
            gold_answer=gold_answer,
            item_metadata=item_metadata,
            question_hash=question_hash,
            embedding=embedding,
            evidence=evidence,
            created_by=created_by,
        )
        self.session.add(item)
        self.session.flush()
        return item

    def create(
        self,
        *,
        tier: EvalItemTier,
        suite: str,
        team_id: UUID | None,
        item_input: dict[str, Any],
        solution_id: str | None = None,
        dataset_version: str = "v1",
        gold_answer: dict[str, Any] | None = None,
        item_metadata: dict[str, Any] | None = None,
        question_hash: str | None = None,
        embedding: list[float] | None = None,
        evidence: str | None = None,
        created_by: UUID | None = None,
    ) -> EvalItem:
        """Create a brand-new eval item with a fresh UUIDv7 and current ``valid_from``."""
        return self.insert_new_version(
            item_id=uuid7(),
            valid_from=datetime.now(UTC),
            tier=tier,
            suite=suite,
            team_id=team_id,
            solution_id=solution_id,
            dataset_version=dataset_version,
            item_input=item_input,
            gold_answer=gold_answer,
            item_metadata=item_metadata or {},
            question_hash=question_hash,
            embedding=embedding,
            evidence=evidence,
            created_by=created_by,
        )

    def upsert_by_question_hash(
        self,
        *,
        tier: EvalItemTier,
        suite: str,
        team_id: UUID | None,
        item_input: dict[str, Any],
        question_hash: str,
        solution_id: str | None = None,
        dataset_version: str = "v1",
        gold_answer: dict[str, Any] | None = None,
        item_metadata: dict[str, Any] | None = None,
        embedding: list[float] | None = None,
        evidence: str | None = None,
        created_by: UUID | None = None,
    ) -> tuple[EvalItem, bool]:
        """Return active item for ``question_hash`` or create one; bool flags creation."""
        stmt = select(EvalItem).where(
            EvalItem.valid_to.is_(None),
            EvalItem.question_hash == question_hash,
        )
        if team_id is None:
            stmt = stmt.where(EvalItem.team_id.is_(None))
        else:
            stmt = stmt.where(EvalItem.team_id == team_id)

        existing = self.session.scalar(stmt)
        if existing is not None:
            return existing, False

        return (
            self.create(
                tier=tier,
                suite=suite,
                team_id=team_id,
                solution_id=solution_id,
                dataset_version=dataset_version,
                item_input=item_input,
                gold_answer=gold_answer,
                item_metadata=item_metadata,
                question_hash=question_hash,
                embedding=embedding,
                evidence=evidence,
                created_by=created_by,
            ),
            True,
        )

    def update_tier_if_unchanged(
        self,
        *,
        item_id: UUID,
        expected_current_tier: EvalItemTier,
        new_tier: EvalItemTier,
        gold_answer: dict[str, Any] | None,
    ) -> bool:
        """Close current version and insert a new tier row only if tier is unchanged."""
        current = self.session.scalar(
            select(EvalItem)
            .where(
                EvalItem.item_id == item_id,
                EvalItem.valid_to.is_(None),
            )
            .with_for_update()
        )
        if current is None or current.tier != expected_current_tier:
            return False

        new_valid_from = datetime.now(UTC)
        if new_valid_from <= current.valid_from:
            new_valid_from = current.valid_from + timedelta(microseconds=1)

        self.set_valid_to(
            item_id=current.item_id,
            valid_from=current.valid_from,
            valid_to=new_valid_from,
        )
        self.insert_new_version(
            item_id=current.item_id,
            valid_from=new_valid_from,
            tier=new_tier,
            suite=current.suite,
            team_id=current.team_id,
            solution_id=current.solution_id,
            dataset_version=current.dataset_version,
            item_input=current.item_input,
            gold_answer=gold_answer,
            item_metadata=current.item_metadata,
            question_hash=current.question_hash,
            embedding=current.embedding,
            evidence=current.evidence,
            created_by=current.created_by,
        )
        return True

    def set_valid_to(self, *, item_id: UUID, valid_from: datetime, valid_to: datetime) -> None:
        """Close out the version identified by ``(item_id, valid_from)`` with ``valid_to``."""
        row = self.session.get(EvalItem, (item_id, valid_from))
        if row is None:
            return
        row.valid_to = valid_to
        self.session.flush()

    def get_active(self, item_id: UUID) -> EvalItem | None:
        """Return the currently active (open-ended) version of ``item_id`` or None."""
        return self.session.scalar(
            select(EvalItem).where(
                EvalItem.item_id == item_id,
                EvalItem.valid_to.is_(None),
            )
        )

    def get_version(self, item_id: UUID, valid_from: datetime) -> EvalItem | None:
        """Return the eval item version with the given composite key, or None."""
        return self.session.get(EvalItem, (item_id, valid_from))

    def list_history(self, item_id: UUID) -> list[EvalItem]:
        """Return all versions of ``item_id`` ordered by ``valid_from`` ascending."""
        return list(
            self.session.scalars(
                select(EvalItem).where(EvalItem.item_id == item_id).order_by(EvalItem.valid_from)
            )
        )

    def list_active(
        self,
        *,
        suite: str | None = None,
        tier: EvalItemTier | None = None,
        team_id: UUID | None = _UNSET,
    ) -> list[EvalItem]:
        """Return active items optionally filtered by suite, tier, and team scope."""
        stmt = select(EvalItem).where(EvalItem.valid_to.is_(None))
        if suite is not None:
            stmt = stmt.where(EvalItem.suite == suite)
        if tier is not None:
            stmt = stmt.where(EvalItem.tier == tier)
        if team_id is not _UNSET:
            if team_id is None:
                stmt = stmt.where(EvalItem.team_id.is_(None))
            else:
                stmt = stmt.where(or_(EvalItem.team_id == team_id, EvalItem.team_id.is_(None)))
        return list(self.session.scalars(stmt.order_by(EvalItem.item_id)))

    def iter_active_with_suite_ids(
        self,
        *,
        page_size: int,
    ) -> Iterator[list[tuple[EvalItem, UUID | None]]]:
        """Yield pages of active items joined with their suite ids, ``page_size`` rows each."""
        offset = 0
        while True:
            stmt = (
                select(EvalItem, EvalItemSuite.suite_id)
                .outerjoin(EvalItemSuite, EvalItemSuite.item_id == EvalItem.item_id)
                .where(EvalItem.valid_to.is_(None))
                .order_by(EvalItem.item_id, EvalItemSuite.suite_id)
                .offset(offset)
                .limit(page_size)
            )
            rows = [(item, suite_id) for item, suite_id in self.session.execute(stmt)]
            if not rows:
                return
            yield rows
            offset += page_size
