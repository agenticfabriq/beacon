"""What a regrade changed, so a number never moves without a record."""

from __future__ import annotations

from typing import Any
from uuid import UUID  # noqa: TC003

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base, IdMixin, TimestampsMixin


class RegradeEvent(Base, IdMixin, TimestampsMixin):
    """One completed regrade: the derivation it ran under and what moved.

    ``Result.outcome`` is a CACHE of a derivation that is stored as if it were
    a fact, and every published rate gates on it. A regrade overwrites it in
    place, so before this table existed a number could move with the reason
    living only in a terminal someone had already closed. The verdicts behind
    it survive -- they are append-only and versioned -- but "which derivation
    produced the number I published" was not written down anywhere, which made
    recovery an investigation instead of a lookup.

    Written in the SAME transaction as the outcomes it describes, so an event
    exists exactly when the change did. A dry run rolls back and therefore
    records nothing, which is the correct behaviour rather than an omission:
    it changed nothing to explain.

    ``team_id`` and RLS arrived in ``0024``, when the History route made this
    table something an API serves. 0022 shipped it without either and named
    the condition for adding them -- "serving it would need a tenant column
    and a policy FIRST" -- so this is that condition being met rather than a
    correction. The backfill was honest because every row had a ``suite_id``
    to inherit from; the migration refuses rather than defaults if that stops
    being true.
    """

    __tablename__ = "regrade_events"

    # A SNAPSHOT of who owned the regrade, not a join key. `suite_id` below is
    # ON DELETE SET NULL, so reading tenancy through the suite would lose it
    # the moment a suite is deleted -- and the history explaining a published
    # number should outlive the suite's row.
    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )

    # The derivation. Both halves, because they move independently and the
    # version alone is the wrong key: 90 of the 93 outcomes corrected on
    # 2026-09-04 changed because the suite's headline METRIC changed while the
    # grader version stayed at v8 (B74/B75).
    suite_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("suites.id", ondelete="SET NULL"), nullable=True
    )
    suite_name: Mapped[str] = mapped_column(String(200), nullable=False)
    grader: Mapped[str] = mapped_column(String(100), nullable=False)
    grader_version: Mapped[str] = mapped_column(String(50), nullable=False)
    headline_metric: Mapped[str] = mapped_column(String(100), nullable=False)

    # The same counters the summary prints. Stored so the record stands alone:
    # a reader asking why a rate moved should not have to re-run anything.
    n_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_graded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_already_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_refused: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_flipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # One entry per outcome that actually moved: result, run, item, before,
    # after, and which path derived it. The BEFORE value is the whole point --
    # with it the prior rate is arithmetic, and without it recovery means
    # re-deriving from verdicts and hoping the derivation was reconstructed
    # correctly. Only flipped rows are recorded, so this stays small: 93 for
    # the spider2 correction, 298 for bird.
    outcome_changes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    # Why it was run, in the operator's words. Free text because the useful
    # version is "B74 headline wiring", which no enum would have anticipated.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # The recorded changes ARE the flips. A count that disagrees with its
        # own evidence is the failure this table exists to prevent, so it is
        # refused rather than stored.
        CheckConstraint(
            "n_flipped = jsonb_array_length(outcome_changes)",
            name="ck_regrade_event_flips_match_changes",
        ),
        CheckConstraint(
            "n_runs >= 0 AND n_graded >= 0 AND n_already_current >= 0"
            " AND n_skipped >= 0 AND n_refused >= 0 AND n_flipped >= 0",
            name="ck_regrade_event_counts_non_negative",
        ),
        # By NAME for `regrade_history.py`, which an operator calls with the
        # name they have; by ID for the route, whose URL carries a UUID. Not
        # interchangeable: a suite deleted and recreated under one name would
        # otherwise serve the previous suite's history.
        Index("idx_regrade_events_suite", "suite_name", "created_at"),
        Index("idx_regrade_events_suite_id", "suite_id", "created_at"),
        Index("idx_regrade_events_team", "team_id"),
    )
