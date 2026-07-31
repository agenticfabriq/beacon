"""Enable RLS on tenancy tables.

A user can see:
  - their own User row
  - teams where they have any membership
  - projects where they have team-level or project-level membership
  - memberships and API keys where they are the user

Connections that do not set app.current_user_id bypass the policies.

Revision ID: 0002_rls_policies
Revises: 0001_tenancy
"""

from __future__ import annotations

from alembic import op

revision = "0002_rls_policies"
down_revision = "0001_tenancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Enable row-level security and install per-tenancy-table policies."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_user_id() RETURNS uuid AS $$
        BEGIN
            RETURN NULLIF(current_setting('app.current_user_id', true), '')::uuid;
        EXCEPTION WHEN OTHERS THEN
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql STABLE;
        """
    )

    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY users_self ON users FOR ALL
        USING (current_user_id() IS NULL OR id = current_user_id());
        """
    )

    op.execute("ALTER TABLE teams ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE teams FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY teams_visible ON teams FOR ALL
        USING (
            current_user_id() IS NULL
            OR EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = current_user_id()
                  AND (
                    (m.scope_kind = 'team' AND m.scope_id = teams.id)
                    OR (
                        m.scope_kind = 'project'
                        AND m.scope_id IN (SELECT id FROM projects WHERE team_id = teams.id)
                    )
                  )
            )
        );
        """
    )

    op.execute("ALTER TABLE projects ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE projects FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY projects_visible ON projects FOR ALL
        USING (
            current_user_id() IS NULL
            OR EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = current_user_id()
                  AND (
                    (m.scope_kind = 'team' AND m.scope_id = projects.team_id)
                    OR (m.scope_kind = 'project' AND m.scope_id = projects.id)
                  )
            )
        );
        """
    )

    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE memberships FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY memberships_self ON memberships FOR ALL
        USING (current_user_id() IS NULL OR user_id = current_user_id());
        """
    )

    op.execute("ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE api_keys FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY api_keys_self ON api_keys FOR ALL
        USING (current_user_id() IS NULL OR user_id = current_user_id());
        """
    )


def downgrade() -> None:
    """Drop RLS policies and disable row-level security on tenancy tables."""
    op.execute("DROP POLICY IF EXISTS api_keys_self ON api_keys;")
    op.execute("DROP POLICY IF EXISTS memberships_self ON memberships;")
    op.execute("DROP POLICY IF EXISTS projects_visible ON projects;")
    op.execute("DROP POLICY IF EXISTS teams_visible ON teams;")
    op.execute("DROP POLICY IF EXISTS users_self ON users;")
    op.execute("ALTER TABLE api_keys NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE memberships NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE projects NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE teams NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE users NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE api_keys DISABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE memberships DISABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE projects DISABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE teams DISABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY;")
    op.execute("DROP FUNCTION IF EXISTS current_user_id();")
