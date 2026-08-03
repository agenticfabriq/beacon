"""Let a run be invalidated instead of deleted.

A bad experiment -- a misconfigured harness, the wrong benchmark database --
otherwise stays in every aggregate forever, or is removed with SQL. Neither is
right: a tracker whose operator can erase inconvenient results cannot be cited,
and deleting collapses "this run was invalid" into "this run never happened",
the same conflation that made an errored arm read as a score of zero.

So the row stays, with who invalidated it, when, and why, and leaves the
aggregates. The reason is NOT NULL-by-convention at the service layer rather
than the column: rows written before this have no reason to record, and
back-filling one would be inventing a justification nobody gave.

Revision ID: 0014_run_invalidation
Revises: 0013_drop_curation
Create Date: 2026-08-02 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_run_invalidation"
down_revision = "0013_drop_curation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the invalidation columns and an index for the default filter."""
    op.add_column("runs", sa.Column("invalidated_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("invalidated_by", sa.Uuid(), nullable=True))
    op.add_column("runs", sa.Column("invalidation_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_runs_invalidated_by_users",
        "runs",
        "users",
        ["invalidated_by"],
        ["id"],
    )
    # Every listing and aggregate filters on this, so the common case -- a run
    # that is still valid -- should not scan the invalidated ones.
    op.create_index(
        "ix_runs_valid",
        "runs",
        ["project_id"],
        postgresql_where=sa.text("invalidated_at IS NULL"),
    )


def downgrade() -> None:
    """Drop the invalidation columns, losing every recorded reason."""
    op.drop_index("ix_runs_valid", table_name="runs")
    op.drop_constraint("fk_runs_invalidated_by_users", "runs", type_="foreignkey")
    op.drop_column("runs", "invalidation_reason")
    op.drop_column("runs", "invalidated_by")
    op.drop_column("runs", "invalidated_at")
