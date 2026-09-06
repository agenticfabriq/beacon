"""Give ``regrade_events`` a tenant column and RLS, because it is about to be served.

0022 created this table without either, and said why: operator-written
metadata that no API route serves needs no tenancy, on the same footing as
``DatasetLoad``. That note also named the condition under which the exemption
expires -- "serving it would need a tenant column and a policy FIRST" -- and
this is that moment. The History page reads these rows over HTTP.

The ordering matters and is the whole reason this is a separate migration
rather than a line in the route's pull request: a routing change does not look
like a tenancy change to whoever reviews it. Ship the column and the policy,
then the route.

**The backfill is honest here, which is not something to assume.** Every
existing row carries a ``suite_id``, and a suite carries a ``team_id``, so
"which team does this regrade belong to" has a real answer rather than a
convenient one. Verified before writing this: one row, ``suite_id`` non-null,
resolving to bird's owning team. The upgrade re-checks that at run time and
REFUSES rather than defaulting -- a tenant column filled with a guess is worse
than no tenant column, because the guess is invisible afterwards and a policy
built on it silently grants or denies.

``suite_id`` is ``ON DELETE SET NULL``, so a future row can lose its link. That
is exactly why ``team_id`` is stored rather than joined through on read: it is
a snapshot of who owned the regrade, and it survives the suite being deleted.
``ON DELETE CASCADE`` on the team, matching ``result_outcomes`` -- if the team
is gone there is nobody the history is for.

Also adds ``(suite_id, created_at)``. The existing index is
``(suite_name, created_at)``, which is the wrong key for the route: the URL
carries a suite UUID, and a suite that is deleted and recreated under the same
name would otherwise show the previous suite's history on the new suite's page.
The name index stays -- ``regrade_history.py`` looks up by name from the
command line, where a name is what an operator has.

Revision ID: 0024_regrade_events_tenancy
Revises: 0023_outcome_history
Create Date: 2026-09-05 21:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_regrade_events_tenancy"
down_revision = "0023_outcome_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "regrade_events",
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # Backfill through the suite, which is the only honest source.
    op.execute(
        """
        UPDATE regrade_events e
        SET team_id = s.team_id
        FROM suites s
        WHERE s.id = e.suite_id AND e.team_id IS NULL
        """
    )

    # REFUSE rather than default. A row that cannot be attributed is a row
    # whose tenancy nobody knows, and inventing one here would be invisible
    # from the moment this migration finishes.
    orphans = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM regrade_events WHERE team_id IS NULL"))
        .scalar_one()
    )
    if orphans:
        raise RuntimeError(
            f"{orphans} regrade_events row(s) have no suite to inherit a team from. "
            "Attribute or delete them by hand before running this migration -- "
            "guessing a tenant is worse than not having the column."
        )

    op.alter_column("regrade_events", "team_id", nullable=False)
    op.create_foreign_key(
        "fk_regrade_events_team",
        "regrade_events",
        "teams",
        ["team_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("idx_regrade_events_team", "regrade_events", ["team_id"])
    # The key the ROUTE queries by. See the module docstring for why the URL's
    # suite UUID and not `suite_name`.
    op.create_index("idx_regrade_events_suite_id", "regrade_events", ["suite_id", "created_at"])

    # Same shape as `result_outcomes` in 0023, and the same caveat applies:
    # `m.user_id = current_user_id()` is defence in depth, not the isolation,
    # because `memberships` is itself FORCE RLS and the subquery therefore only
    # ever sees the caller's own rows. It stays because this table's isolation
    # otherwise depends invisibly on another table's policy.
    op.execute("ALTER TABLE regrade_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE regrade_events FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY regrade_events_visible ON regrade_events FOR ALL
        USING (
            current_user_id() IS NULL
            OR EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = current_user_id()
                  AND m.scope_kind = 'team'
                  AND m.scope_id = regrade_events.team_id
            )
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS regrade_events_visible ON regrade_events;")
    op.execute("ALTER TABLE regrade_events NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE regrade_events DISABLE ROW LEVEL SECURITY;")
    op.drop_index("idx_regrade_events_suite_id", table_name="regrade_events")
    op.drop_index("idx_regrade_events_team", table_name="regrade_events")
    op.drop_constraint("fk_regrade_events_team", "regrade_events", type_="foreignkey")
    op.drop_column("regrade_events", "team_id")
