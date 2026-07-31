"""attributions table.

Revision ID: 0005_attributions
Revises: 0004_rls_runs
Create Date: 2026-06-05 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_attributions"
down_revision = "0004_rls_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the attributions table with indexes and RLS policy."""
    op.create_table(
        "attributions",
        sa.Column("attribution_id", sa.Uuid(), primary_key=True),
        sa.Column("sweep_id", sa.Uuid(), nullable=False),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            sa.Uuid(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "solution_id",
            sa.Uuid(),
            sa.ForeignKey("solutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("solution_version", sa.String(100), nullable=False),
        sa.Column("suite", sa.String(200), nullable=False),
        sa.Column("dataset_version", sa.String(100), nullable=False),
        sa.Column("layer_name", sa.String(100), nullable=False),
        sa.Column("methodology", sa.String(20), nullable=False, server_default=sa.text("'LOO'")),
        sa.Column("baseline_run_id", sa.Uuid(), nullable=False),
        sa.Column("ablated_run_id", sa.Uuid(), nullable=False),
        sa.Column("pass_at_k_baseline", JSONB(), nullable=False),
        sa.Column("pass_at_k_ablated", JSONB(), nullable=False),
        sa.Column("delta_pass_at_k", JSONB(), nullable=False),
        sa.Column("pass_hat_k_baseline", JSONB(), nullable=False),
        sa.Column("pass_hat_k_ablated", JSONB(), nullable=False),
        sa.Column("delta_pass_hat_k", JSONB(), nullable=False),
        sa.Column("token_delta_pct", sa.Numeric(), nullable=True),
        sa.Column("runtime_delta_pct", sa.Numeric(), nullable=True),
        sa.Column("mcnemar_p", sa.Numeric(), nullable=False),
        sa.Column("bh_adjusted_p", sa.Numeric(), nullable=True),
        sa.Column("ci_low", sa.Numeric(), nullable=False),
        sa.Column("ci_high", sa.Numeric(), nullable=False),
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
        sa.CheckConstraint(
            "methodology IN ('LOO', 'shapley')",
            name="ck_attribution_methodology",
        ),
        sa.UniqueConstraint("sweep_id", "layer_name", name="uq_attribution_sweep_layer"),
    )
    op.create_index("idx_attributions_sweep", "attributions", ["sweep_id"])
    op.create_index(
        "idx_attributions_project_solution",
        "attributions",
        ["project_id", "solution_id", "suite"],
    )

    op.execute("ALTER TABLE attributions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE attributions FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY attributions_visible ON attributions FOR ALL
        USING (
            current_user_id() IS NULL
            OR EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = current_user_id()
                  AND (
                    (m.scope_kind = 'team' AND m.scope_id = attributions.team_id)
                    OR (m.scope_kind = 'project' AND m.scope_id = attributions.project_id)
                  )
            )
        );
        """
    )


def downgrade() -> None:
    """Drop the attributions table and its RLS policy."""
    op.execute("DROP POLICY IF EXISTS attributions_visible ON attributions;")
    op.execute("ALTER TABLE attributions NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE attributions DISABLE ROW LEVEL SECURITY;")
    op.drop_index("idx_attributions_project_solution", table_name="attributions")
    op.drop_index("idx_attributions_sweep", table_name="attributions")
    op.drop_table("attributions")
