"""Add project_solutions link table.

Revision ID: 0009_project_solutions
Revises: 0008_trace_retention_summary
Create Date: 2026-06-10 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_project_solutions"
down_revision = "0008_trace_retention_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the project_solutions link table with indexes and RLS policy."""
    op.create_table(
        "project_solutions",
        sa.Column(
            "team_id",
            sa.Uuid(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "solution_id",
            sa.Uuid(),
            sa.ForeignKey("solutions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "added_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_project_solution_team", "project_solutions", ["team_id"])
    op.create_index("ix_project_solution_project", "project_solutions", ["project_id"])
    op.create_index("ix_project_solution_solution", "project_solutions", ["solution_id"])

    bypass = "current_user_id() IS NULL"
    visible_via_project = """
        EXISTS (
            SELECT 1 FROM memberships m
            WHERE m.user_id = current_user_id()
              AND (
                (m.scope_kind = 'team' AND m.scope_id = project_solutions.team_id)
                OR (m.scope_kind = 'project' AND m.scope_id = project_solutions.project_id)
              )
        )
    """
    op.execute("ALTER TABLE project_solutions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE project_solutions FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY project_solutions_visible ON project_solutions FOR ALL
        USING ({bypass} OR {visible_via_project});
        """
    )


def downgrade() -> None:
    """Drop the project_solutions table and its RLS policy."""
    op.execute("DROP POLICY IF EXISTS project_solutions_visible ON project_solutions;")
    op.execute("ALTER TABLE project_solutions NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE project_solutions DISABLE ROW LEVEL SECURITY;")
    op.drop_index("ix_project_solution_solution", table_name="project_solutions")
    op.drop_index("ix_project_solution_project", table_name="project_solutions")
    op.drop_index("ix_project_solution_team", table_name="project_solutions")
    op.drop_table("project_solutions")
