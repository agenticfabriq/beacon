"""Enable RLS on solutions, runs, results, verdicts, traces.

Reuses current_user_id() defined in 0002_rls_policies.

Revision ID: 0004_rls_runs
Revises: 0003_solutions_runs_results
"""

from __future__ import annotations

from alembic import op

revision = "0004_rls_runs"
down_revision = "0003_solutions_runs_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Enable RLS and install policies on solutions, runs, results, verdicts, traces."""
    bypass = "current_user_id() IS NULL"
    visible_via_team = """
        EXISTS (
            SELECT 1 FROM memberships m
            WHERE m.user_id = current_user_id()
              AND m.scope_kind = 'team'
              AND m.scope_id = {team_col}
        )
    """
    visible_via_project = """
        EXISTS (
            SELECT 1 FROM memberships m
            WHERE m.user_id = current_user_id()
              AND (
                (m.scope_kind = 'team' AND m.scope_id = {team_col})
                OR (m.scope_kind = 'project' AND m.scope_id = {project_col})
              )
        )
    """

    op.execute("ALTER TABLE solutions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE solutions FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY solutions_visible ON solutions FOR ALL
        USING ({bypass} OR {visible_via_team.format(team_col="solutions.team_id")});
        """
    )

    for table in ("runs", "results", "verdicts", "traces"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {table}_visible ON {table} FOR ALL
            USING ({bypass} OR {
                visible_via_project.format(
                    team_col=f"{table}.team_id",
                    project_col=f"{table}.project_id",
                )
            });
            """
        )


def downgrade() -> None:
    """Drop RLS policies and disable row-level security on the run tables."""
    for table in ("solutions", "runs", "results", "verdicts", "traces"):
        op.execute(f"DROP POLICY IF EXISTS {table}_visible ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
