"""worker_state and antigoodhart_findings.

Revision ID: 0007_workers_antigoodhart
Revises: 0006_registry
Create Date: 2026-06-05 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_workers_antigoodhart"
down_revision = "0006_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the worker_state and antigoodhart_findings tables."""
    op.create_table(
        "worker_state",
        sa.Column("worker_name", sa.String(64), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("last_processed_id", sa.Uuid(), nullable=True),
        sa.Column("last_processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("tick_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_error_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "uq_worker_state_worker_team",
        "worker_state",
        ["worker_name", "team_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index("ix_worker_state_updated_at", "worker_state", ["updated_at"])

    op.create_table(
        "antigoodhart_findings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scan_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("suite_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('sql_in_question','evidence_leak','metadata_bleed','distribution_skew')",
            name="ck_antigoodhart_kind",
        ),
        sa.CheckConstraint(
            "severity IN ('low','medium','high')",
            name="ck_antigoodhart_severity",
        ),
    )
    op.create_index("ix_antigoodhart_scan", "antigoodhart_findings", ["scan_id"])
    op.create_index("ix_antigoodhart_item", "antigoodhart_findings", ["item_id"])
    op.create_index(
        "ix_antigoodhart_team_suite",
        "antigoodhart_findings",
        ["team_id", "suite_id"],
    )
    op.create_index(
        "ix_antigoodhart_kind_severity",
        "antigoodhart_findings",
        ["kind", "severity"],
    )
    op.execute("ALTER TABLE antigoodhart_findings ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE antigoodhart_findings FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY antigoodhart_findings_visible ON antigoodhart_findings FOR ALL
        USING (
            current_user_id() IS NULL
            OR antigoodhart_findings.team_id IS NULL
            OR EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = current_user_id()
                  AND (
                    (m.scope_kind = 'team' AND m.scope_id = antigoodhart_findings.team_id)
                    OR (m.scope_kind = 'global' AND m.role = 'beacon_admin')
                  )
            )
        );
        """
    )


def downgrade() -> None:
    """Drop the worker_state and antigoodhart_findings tables."""
    op.execute("DROP POLICY IF EXISTS antigoodhart_findings_visible ON antigoodhart_findings;")
    op.execute("ALTER TABLE antigoodhart_findings NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE antigoodhart_findings DISABLE ROW LEVEL SECURITY;")
    op.drop_index("ix_antigoodhart_kind_severity", table_name="antigoodhart_findings")
    op.drop_index("ix_antigoodhart_team_suite", table_name="antigoodhart_findings")
    op.drop_index("ix_antigoodhart_item", table_name="antigoodhart_findings")
    op.drop_index("ix_antigoodhart_scan", table_name="antigoodhart_findings")
    op.drop_table("antigoodhart_findings")
    op.drop_index("ix_worker_state_updated_at", table_name="worker_state")
    op.drop_index("uq_worker_state_worker_team", table_name="worker_state")
    op.drop_table("worker_state")
