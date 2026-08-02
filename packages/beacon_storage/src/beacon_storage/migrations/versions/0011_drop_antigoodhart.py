"""Drop antigoodhart_findings.

Anti-Goodhart scanned curated eval items for leakage and skew. Curation moves
to verity, whose ``GoldenQuestion`` carries the review workflow beacon's
``EvalItem`` only approximated, so there is no curated corpus here left to
police. See ``docs/2026-08-02-beacon-verity-boundary.md`` in beacon-internal.

``worker_state`` was created by the same migration (0007) and is untouched --
the retention worker still uses it.

Revision ID: 0011_drop_antigoodhart
Revises: 0010_run_sweep_arm
Create Date: 2026-08-02 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_drop_antigoodhart"
down_revision = "0010_run_sweep_arm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop the anti-Goodhart findings table and its RLS policy."""
    op.execute("DROP POLICY IF EXISTS antigoodhart_findings_visible ON antigoodhart_findings;")
    op.execute("ALTER TABLE antigoodhart_findings NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE antigoodhart_findings DISABLE ROW LEVEL SECURITY;")
    op.drop_table("antigoodhart_findings")


def downgrade() -> None:
    """Recreate the table so 0007's schema can be reached again.

    Structure only: the findings themselves are not recoverable, and were
    derived data that a rescan would have regenerated.
    """
    op.create_table(
        "antigoodhart_findings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("detail", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute("ALTER TABLE antigoodhart_findings ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE antigoodhart_findings FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY antigoodhart_findings_visible ON antigoodhart_findings FOR ALL
        USING (
            current_user_id() IS NULL
            OR EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = current_user_id()
                  AND m.scope_kind = 'team' AND m.scope_id = antigoodhart_findings.team_id
            )
        );
        """
    )
