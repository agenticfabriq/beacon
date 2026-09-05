"""Record what every regrade changed, so a number cannot move silently.

``Result.outcome`` is a cache of a derivation stored as if it were a fact, and
every published rate gates on it. A regrade overwrites it in place. The
verdicts behind it survive -- append-only, unique per
``(result, metric, grader, version)`` -- so the EVIDENCE was never lost, but
nothing recorded which derivation produced the number that had already been
published. That made recovery an investigation rather than a lookup, and it
made a moved number indistinguishable from a number that was always that
value.

Demonstrated on the deployment rather than argued: re-deriving spider2's 19
runs from stored verdicts reproduces run ``06a99a7c`` at 7 PASS under
``exact_match`` and 9 under ``got_facts`` -- exactly the 7->9 the register
records for the 2026-09-04 correction. The data was there; the read-back was
not.

This table is the cheap half of the fix and deliberately not the whole one.
It answers "tell me when a number moved and let me look up the old one". It
does NOT make an arbitrary published rate reproducible forever -- that needs a
run-set bound as well, because the matrix pools every valid run of a config
identity with no time bound, so a later run moves a rate with no regrade
involved at all.

``n_flipped`` is constrained to equal ``jsonb_array_length(outcome_changes)``.
A count that disagrees with its own evidence is precisely the failure this
table exists to prevent, so the database refuses it instead of storing it.

Only flipped rows go in ``outcome_changes``, which keeps it small: 93 entries
for the spider2 correction, 298 for the bird regrade this was written for.

**No `team_id` and no RLS policy, deliberately.** Every table that carries
tenant rows is under FORCE RLS with a team-scoped policy (``0004_rls_runs``),
and this one is not, on the same footing as ``dataset_loads``: it is
operational metadata written by an operator script and read by another, never
served over the API. It stores result and run ids but no tenant column, so
there is nothing for a policy to scope on.

Two consequences a future reader should have rather than rediscover. Serving
this over HTTP is NOT a routing change -- it needs `team_id` plus a policy
first, or it leaks which runs exist across tenants. And backfilling that
column later is LOSSY: `suite_id` is `ON DELETE SET NULL`, so a row whose
suite is gone has no path back to a team.

Revision ID: 0022_regrade_events
Revises: 0021_ablation_exclusion_counts
Create Date: 2026-09-04 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_regrade_events"
down_revision = "0021_ablation_exclusion_counts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "regrade_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("suite_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("suite_name", sa.String(200), nullable=False),
        sa.Column("grader", sa.String(100), nullable=False),
        sa.Column("grader_version", sa.String(50), nullable=False),
        sa.Column("headline_metric", sa.String(100), nullable=False),
        sa.Column("n_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_graded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_already_current", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_refused", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_flipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "outcome_changes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        # SET NULL rather than CASCADE: deleting a suite must not delete the
        # record of numbers that were published while it existed.
        sa.ForeignKeyConstraint(["suite_id"], ["suites.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "n_flipped = jsonb_array_length(outcome_changes)",
            name="ck_regrade_event_flips_match_changes",
        ),
        sa.CheckConstraint(
            "n_runs >= 0 AND n_graded >= 0 AND n_already_current >= 0"
            " AND n_skipped >= 0 AND n_refused >= 0 AND n_flipped >= 0",
            name="ck_regrade_event_counts_non_negative",
        ),
    )
    op.create_index("idx_regrade_events_suite", "regrade_events", ["suite_name", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_regrade_events_suite", table_name="regrade_events")
    op.drop_table("regrade_events")
