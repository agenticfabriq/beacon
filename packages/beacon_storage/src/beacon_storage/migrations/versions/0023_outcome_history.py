"""Record what a result scored and under which derivation, going forward.

``Result.outcome`` is one column that every published rate gates on, and a
regrade overwrites it in place. The verdicts behind it survive -- append-only,
unique per ``(result, metric, grader, version)`` -- so the evidence was never
lost, but nothing recorded WHICH rule produced the value that was published.
That is why a ``?grader_version=`` filter could not work as asked: it would
rewind the verdict-backed columns (``exact_rate``, ``got_facts_rate``) and
leave the headline ``ex_rate`` untouched, because that one reads
``Result.outcome``, which carries no version.

These tables make the headline rewindable. This migration is the WRITE half
only, shipped first on purpose: the expensive part is teaching the fifteen
places in ``matrix.py`` that gate on ``Result.outcome`` to read as-of a
derivation, and that is worth doing against history that already exists.

**Nothing is backfilled, and nothing can be.** For a result written before
this, the honest answer to "what did it score under v7" is that we do not
know, and the read half must render that as "-" rather than compute a rate
over whatever subset happens to have history. A rate over a partial
denominator is the defect this whole register keeps finding: an absence
rendered as a measurement.

``derivations`` is a dimension table because the KEY is what everyone gets
wrong. The grader version alone is NOT the derivation: 90 of the 93 outcomes
corrected on 2026-09-04 moved while the version stayed at v8, because the
suite's headline METRIC changed (B74/B75). A filter keyed on version would
have reported that nothing happened. ``grader`` and ``grader_version`` are nullable TOGETHER,
which means "no grader decided this" -- an error, a timeout, a refusal
contract, a judge-only rubric. Naming a grader that had no say would be a
fabrication, so the row says nothing instead. ``metric`` is nullable on its
own, because a grader declaring no metric decides by being the first
execution verdict and has none to name -- requiring all three would crash
ingest on that grader, which is reachable. NULLS NOT DISTINCT (Postgres
15+) makes that collapse to one row rather than a fresh one per insert.

``result_outcomes`` is written ON CHANGE, including the first write, which is
a change from nothing. Recording every derivation for every result would cost
127,401 rows per regrade for a value that is stable almost everywhere; on
change, the bird regrade would have written 298. So reading "the outcome at
derivation D" is the latest row at or before D, not a point lookup.

There is deliberately NO unique constraint on ``(result_id, derivation_id)``.
A re-push regrades the same result under the same derivation against
DIFFERENT evidence, and that second answer is real history, not a duplicate
to reject.

Revision ID: 0023_outcome_history
Revises: 0022_regrade_events
Create Date: 2026-09-05 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023_outcome_history"
down_revision = "0022_regrade_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "derivations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("grader", sa.String(100), nullable=True),
        sa.Column("grader_version", sa.String(100), nullable=True),
        sa.Column("metric", sa.String(100), nullable=True),
        sa.CheckConstraint(
            "(grader IS NULL) = (grader_version IS NULL)"
            " AND (grader IS NOT NULL OR metric IS NULL)",
            name="ck_derivation_grader_and_version_together",
        ),
    )
    # NULLS NOT DISTINCT has no SQLAlchemy-portable spelling in a migration, so
    # the constraint is issued directly. Without it Postgres treats every
    # all-null row as unique and the "no grader decided" derivation would get a
    # new row per push -- millions of rows meaning one thing.
    op.execute(
        "ALTER TABLE derivations ADD CONSTRAINT uq_derivation_key "
        "UNIQUE NULLS NOT DISTINCT (grader, grader_version, metric)"
    )

    op.create_table(
        "result_outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("derivation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "recorded_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["result_id"], ["results.id"], ondelete="CASCADE"),
        # RESTRICT, not CASCADE: a derivation is referenced by history that
        # explains published numbers, so it must not be deletable out from
        # under them.
        sa.ForeignKeyConstraint(["derivation_id"], ["derivations.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "source IN ('ingest', 'regrade', 'harness')",
            name="ck_result_outcome_source",
        ),
    )
    # THREE columns, matching the model. `recorded_at` defaults to now(),
    # which is transaction-scoped, so every row one regrade writes shares a
    # timestamp: ordering by it alone ties and a reader resolving the tie
    # arbitrarily reads back the wrong value as latest. uuid7 sorts by
    # creation. An earlier attempt to fix this silently did not apply,
    # because the formatter had collapsed this call onto one line.
    op.create_index(
        "idx_result_outcomes_result",
        "result_outcomes",
        ["result_id", "recorded_at", "id"],
    )
    op.create_index("idx_result_outcomes_derivation", "result_outcomes", ["derivation_id"])
    op.create_index("idx_result_outcomes_team", "result_outcomes", ["team_id"])

    # RLS, alongside `results` and `verdicts` (0004). NOT exempt the way
    # `regrade_events` is: this table is written by the HTTP ingest route and
    # exists to back a matrix filter, so the read half will be a ROUTE -- and
    # a routing change does not look like a tenancy change to whoever writes
    # it. Installed now because a tenant column cannot be backfilled
    # honestly: `results` is ON DELETE CASCADE, so a deleted result leaves
    # no path back to a team.
    #
    # `derivations` deliberately gets none: it is (grader, version, metric)
    # and nothing else, so it carries no tenant data to scope.
    op.execute("ALTER TABLE result_outcomes ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE result_outcomes FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY result_outcomes_visible ON result_outcomes FOR ALL
        USING (
            current_user_id() IS NULL
            OR EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = current_user_id()
                  AND m.scope_kind = 'team'
                  AND m.scope_id = result_outcomes.team_id
            )
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS result_outcomes_visible ON result_outcomes;")
    op.execute("ALTER TABLE result_outcomes NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE result_outcomes DISABLE ROW LEVEL SECURITY;")
    op.drop_index("idx_result_outcomes_team", table_name="result_outcomes")
    op.drop_index("idx_result_outcomes_derivation", table_name="result_outcomes")
    op.drop_index("idx_result_outcomes_result", table_name="result_outcomes")
    op.drop_table("result_outcomes")
    op.execute("ALTER TABLE derivations DROP CONSTRAINT IF EXISTS uq_derivation_key")
    op.drop_table("derivations")
