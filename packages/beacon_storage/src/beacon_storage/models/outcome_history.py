"""Outcome history: what a result scored, under which derivation.

``Result.outcome`` is a single column that every published rate gates on, and
it is overwritten in place. So the number you published last week is not in
the database this week -- not because the evidence was destroyed (verdicts are
append-only and versioned) but because nothing recorded WHICH rule produced
the value that went out.

These two tables record it going forward. They are the write half of the fix,
deliberately shipped before the read half: the expensive part is teaching the
15 places in ``matrix.py`` that gate on ``Result.outcome`` to read as-of a
derivation, and that work is worth doing against data that already exists.
Nothing is backfilled and nothing can be -- for results written before this,
the honest answer to "what was it under v7" is that we do not know.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base, IdMixin, TimestampsMixin


class Derivation(Base, IdMixin, TimestampsMixin):
    """One rule for turning evidence into PASS/FAIL.

    A dimension table rather than three columns repeated on every history row,
    for two reasons. The rows stay narrow, which matters when the history is
    one row per outcome change across 127k results. And it makes the KEY
    explicit, which is the thing everyone gets wrong: **the grader version
    alone is not the derivation.** 90 of the 93 outcomes corrected on
    2026-09-04 moved while the version stayed at v8, because the suite's
    headline METRIC changed (B74/B75). A filter keyed on version would have
    reported that nothing happened.

    ``grader`` and ``grader_version`` are nullable TOGETHER, and that pair
    being null means "no grader decided this outcome" -- an error, a timeout, a
    refusal contract, a judge-only rubric. Those outcomes are real and are not
    a grader's, so naming a grader that had no say would be a fabrication.

    ``metric`` is nullable INDEPENDENTLY, because a grader that declares no
    metric decides by being the first execution verdict and has no metric to
    name. A metric without a grader is refused: there is nothing for it to be
    a reading of.

    The unique constraint is NULLS NOT DISTINCT (Postgres 15+) so that the
    no-grader derivation collapses to ONE row rather than a new one per
    insert.

    NO tenant column and no RLS, and here that is safe rather than deferred:
    a derivation is ``(grader, version, metric)`` and nothing else. Knowing
    that "result_set_match v8 exact_match" exists reveals nothing about any
    team's runs. ``result_outcomes``, which does carry tenant data, is
    protected.
    """

    __tablename__ = "derivations"

    grader: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 100 to match `verdicts.grader_version`, NOT 50. This write is inside the
    # ingest transaction, so a narrower column turns a version string that
    # stores fine in `verdicts` into a 500 on the push.
    grader_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metric: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "grader",
            "grader_version",
            "metric",
            name="uq_derivation_key",
            postgresql_nulls_not_distinct=True,
        ),
        # A grader and its version travel together -- either without the other
        # is incoherent. The METRIC is independently nullable, because a
        # grader that declares no metric decides by being the first execution
        # verdict, and its derivation genuinely has no metric to name. An
        # earlier version of this required all three or none, which would have
        # crashed ingest on exactly that grader: verified by construction, a
        # metric-less grader does produce a deciding verdict.
        #
        # A metric with no grader is still refused: there is nothing for the
        # metric to be a reading OF.
        CheckConstraint(
            "(grader IS NULL) = (grader_version IS NULL)"
            " AND (grader IS NOT NULL OR metric IS NULL)",
            name="ck_derivation_grader_and_version_together",
        ),
    )


class ResultOutcome(Base, IdMixin):
    """One outcome a result held, under one derivation.

    Append-only, and written ON CHANGE -- including the first write, which is
    a change from nothing. Recording every derivation for every result would
    cost 127k rows per regrade for a value that is stable almost everywhere;
    on change, the bird regrade of 2026-09-04 would have written 298.

    Reading "the outcome at derivation D" is therefore the latest row at or
    before D, not a direct lookup. There is deliberately NO unique constraint
    on ``(result_id, derivation_id)``: a re-push regrades the same result under
    the same derivation against different evidence, and that second answer is
    real history rather than a duplicate to reject.

    ``source`` says which path wrote it, because "the number changed and no
    regrade ran" and "the number changed in a regrade" are different
    incidents, and the run-set gap means the first one happens.
    """

    __tablename__ = "result_outcomes"

    # A TENANT column, and it has to exist from the first migration. 0022's
    # note is the reason: backfilling one later is lossy, and unlike
    # `regrade_events` this table is written by the HTTP ingest route and its
    # whole purpose is to back a matrix filter. So the exemption that covers an
    # operator-only table does not transfer -- whoever ships the read half adds
    # a ROUTE, not a column, and a routing change does not look like a tenancy
    # change. RLS is installed in 0023 alongside `results` and `verdicts`.
    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    result_id: Mapped[UUID] = mapped_column(
        ForeignKey("results.id", ondelete="CASCADE"), nullable=False
    )
    derivation_id: Mapped[UUID] = mapped_column(
        ForeignKey("derivations.id", ondelete="RESTRICT"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "source IN ('ingest', 'regrade', 'harness')",
            name="ck_result_outcome_source",
        ),
        # "This result's history, newest first" -- and `id` is part of the key,
        # not a decoration. `recorded_at` defaults to `now()`, which is
        # TRANSACTION-scoped, so every row a single regrade writes carries the
        # same timestamp. Ordering by `recorded_at` alone leaves ties, and the
        # read half resolving a tie arbitrarily reads back the wrong value as
        # latest. uuid7 ids sort by creation, so they break it correctly.
        Index("idx_result_outcomes_result", "result_id", "recorded_at", "id"),
        Index("idx_result_outcomes_derivation", "derivation_id"),
        Index("idx_result_outcomes_team", "team_id"),
    )
