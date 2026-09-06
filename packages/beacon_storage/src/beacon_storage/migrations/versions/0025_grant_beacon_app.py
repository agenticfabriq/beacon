"""Give ``beacon_app`` the privileges it needs, so RLS can start doing something.

Every RLS policy in this schema currently constrains nothing where it is
deployed. The app connects as ``beacon``, which is the role
``docker-compose.yml`` creates as ``POSTGRES_USER`` and therefore owns the
cluster: it carries ``rolsuper`` and ``rolbypassrls``, and a superuser's
queries never consult a policy. Measured before writing this -- a caller
holding no membership at all read every row of ``regrade_events``.
``FORCE ROW LEVEL SECURITY`` does not help: that makes policies apply to a
table's OWNER, not to a superuser.

``beacon_app`` is the constrained role the tests drop to. Until now no
migration granted it anything, so it existed only inside the test fixture and
the policies were exercised only against a role production never used. This
migration is the half of the fix that belongs in the schema. The other half is
pointing the app's DSN at it, which is a deployment change.

**What this does NOT do, deliberately.** It does not set a password. A role
created here can be granted privileges but cannot authenticate until an
operator gives it a secret out of band, so a checked-in migration never
carries one. And it does not touch ``DATABASE_URL``: switching the running app
is a separate step, and it needed one more migration first. Under this one
alone RLS refused the WRITES -- the tenant policies had no ``WITH CHECK``, so
Postgres reused USING as the insert check and a team could not be created by
someone not yet a member of it. 0026 fixes that, and the serving DSN is
switched there.

**Why the app still needs a second, owning role.** Migrations run DDL and the
test fixtures drop and recreate the schema; neither is something the serving
role should be able to do. So ``beacon`` runs migrations and ``beacon_app``
serves -- see ``MIGRATE_DATABASE_URL`` in the Makefile.

**Why this is safe with respect to authentication.** The policies read
``current_user_id() IS NULL OR ...``, so a connection that never sets
``app.current_user_id`` sees everything. That branch is load-bearing rather
than a hole: measured across the mounted routes, 32 of 35 that take a session
resolve a user first (``get_current_user`` sets the GUC), and the three that do
not are the auth endpoints, which must look a user up BEFORE one is known.
None of them serves tenant data. The retention worker and the seed command
rely on the same branch, correctly -- they act for no user.

``ALTER DEFAULT PRIVILEGES`` is issued for the migration role, so a table
created by a LATER migration is reachable without another grant migration.
Without it, the next ``create_table`` would ship a table the serving role
cannot read, and the failure would arrive as a runtime permission error on
whichever route touched it first.

Revision ID: 0025_grant_beacon_app
Revises: 0024_regrade_events_tenancy
Create Date: 2026-09-06 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_grant_beacon_app"
down_revision = "0024_regrade_events_tenancy"
branch_labels = None
depends_on = None

# A module constant, never a parameter. Postgres has no placeholder for an
# IDENTIFIER in GRANT or ALTER ROLE, so every statement below interpolates it
# -- see the S608 note in ruff.toml, which covers this directory. That is sound
# only because this value is fixed here: a role name taken from anywhere a
# caller can reach would make every one of those statements an injection
# point.
APP_ROLE = "beacon_app"


def upgrade() -> None:
    bind = op.get_bind()

    # Created without LOGIN privileges of any use: no password, so it cannot
    # authenticate until an operator sets one. Idempotent because the test
    # fixture and any hand-provisioned deployment may already have it, and
    # Postgres has no CREATE ROLE IF NOT EXISTS.
    # Existence checked HERE rather than in a DO block. A DO block cannot take
    # parameters, so the role name had to be interpolated into one; asking
    # Postgres from Python takes a bind parameter and leaves exactly one
    # interpolated statement, guarded below.
    exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE}
    ).scalar()
    if not exists:
        op.execute(f"CREATE ROLE {APP_ROLE}")

    # LOGIN is set SEPARATELY and unconditionally, not as part of CREATE. The
    # role may already exist without it -- the test fixture creates a bare
    # `CREATE ROLE beacon_app`, and roles are cluster-wide, so an
    # IF-NOT-EXISTS guard around a `CREATE ROLE ... LOGIN` skips the create and
    # leaves the role unable to serve. Measured: the app then fails to start
    # with `role "beacon_app" is not permitted to log in`, which reads like a
    # deployment misconfiguration rather than a migration that half-ran.
    #
    # Granting LOGIN with no password is inert: the role still cannot
    # authenticate until an operator sets a secret.
    op.execute(f"ALTER ROLE {APP_ROLE} LOGIN")

    # Refuse to grant to a role that bypasses RLS. The whole point is a
    # constrained serving role, and granting these to a superuser would leave
    # the deployment looking fixed while every policy stayed inert -- the exact
    # condition this migration exists to end, made harder to notice.
    attrs = bind.execute(
        sa.text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"),
        {"r": APP_ROLE},
    ).one()
    if attrs[0] or attrs[1]:
        raise RuntimeError(
            f"{APP_ROLE} has rolsuper={attrs[0]} rolbypassrls={attrs[1]}, so it would "
            "bypass every policy it is being granted access through. Remove those "
            "attributes (ALTER ROLE ... NOSUPERUSER NOBYPASSRLS) before migrating."
        )

    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    # The policies call this, so the serving role must be able to.
    op.execute(f"GRANT EXECUTE ON FUNCTION current_user_id() TO {APP_ROLE}")

    # Future tables, so the next migration does not need one of these.
    migration_role = bind.execute(sa.text("SELECT current_user")).scalar_one()
    defaults = f"ALTER DEFAULT PRIVILEGES FOR ROLE {migration_role} IN SCHEMA public"
    op.execute(f"{defaults} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}")
    op.execute(f"{defaults} GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}")


def downgrade() -> None:
    bind = op.get_bind()
    migration_role = bind.execute(sa.text("SELECT current_user")).scalar_one()
    defaults = f"ALTER DEFAULT PRIVILEGES FOR ROLE {migration_role} IN SCHEMA public"
    op.execute(f"{defaults} REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE}")
    op.execute(f"{defaults} REVOKE USAGE, SELECT ON SEQUENCES FROM {APP_ROLE}")
    op.execute(f"REVOKE EXECUTE ON FUNCTION current_user_id() FROM {APP_ROLE}")
    seqs = "ON ALL SEQUENCES IN SCHEMA public"
    op.execute(f"REVOKE USAGE, SELECT {seqs} FROM {APP_ROLE}")
    all_tables = "ON ALL TABLES IN SCHEMA public"
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE {all_tables} FROM {APP_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    # The ROLE is deliberately left in place. It may predate this migration and
    # may be named in a deployment's DSN; dropping it out from under a running
    # app is not something a schema downgrade should do.
