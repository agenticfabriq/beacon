"""Let the policies express what team administration actually does.

0025 granted a constrained ``beacon_app`` so RLS could start deciding what a
request reads. Pointing the app at it did not work, and this is why: the tenant
policies were written for a reader looking at their OWN rows, and the app also
administers other people's.

Four things were broken, all measured against the live cluster as
``beacon_app``:

* ``INSERT INTO teams`` was refused outright. Policies were ``FOR ALL USING
  (...)`` with no ``WITH CHECK``, and Postgres reuses USING as the insert
  check -- so a new team had to satisfy a membership predicate before its
  first membership existed. ``TeamService.create`` inserts the team and then
  grants the creator, so ``POST /v1/teams`` would 500.
* ``users_self`` is ``id = current_user_id()``, so a lookup of anyone else
  returned nothing. ``add_team_member`` calls ``get_by_email`` to decide
  whether an invitee already exists, so an existing user looked new and the
  insert hit the unique constraint on email.
* ``memberships_self`` is ``user_id = current_user_id()``, so a team's roster
  showed one row -- yours. ``remove_team_member`` and ``issue_member_key``
  both check membership through it and would 404 a real member, and
  ``list_teams``' admin branch would quietly return only the actor's teams
  while its docstring promises all of them.
* ``api_keys_self`` is ``user_id = current_user_id()``, so minting a key for a
  member -- how an invited person gets their first credential -- was refused.

**Per-command policies, not one ``FOR ALL``.** This is the part that a probe
caught and reasoning did not. ``FOR ALL USING`` governs SELECT, UPDATE and
DELETE with ONE predicate, so widening the read to "co-members of my team"
also widened DELETE: measured, a plain member could delete their own team's
admin, leaving the team unadministered. Reads and writes need different
predicates, so each command gets its own policy.

**Why ``current_user_scopes()`` exists.** A membership-aware policy ON
``memberships`` that queries ``memberships`` does not work -- Postgres raises
"infinite recursion detected in policy for relation". The function is
``SECURITY DEFINER``, so it runs as the owner and does not re-enter the
policy. It returns ONLY the caller's own rows, so it leaks nothing, and
``SET search_path = public`` closes the usual ``SECURITY DEFINER`` hole where
a caller shadows a referenced object.

**What is deliberately NOT permitted**, each verified by probe:

* granting yourself membership anywhere -- the obvious ``WITH CHECK
  (user_id = current_user_id())`` is an escalation, not a convenience;
* escalating your own role;
* deleting a co-member, including the admin;
* a team admin writing into a team they do not administer;
* a team admin granting themselves global admin.

**What IS permitted, and is worth knowing.** A team admin can mint an API key
for a member, and that key authenticates AS that member. That is the existing
design -- it is how an invited person receives their first credential, gated
by ``TEAM_MANAGE`` -- and the policy matches the API rather than being
stricter or looser than it. If that should change, it changes in both places.

Revision ID: 0026_admin_policies
Revises: 0025_grant_beacon_app
Create Date: 2026-09-06 15:00:00

The revision id is short deliberately: ``alembic_version.version_num`` is
VARCHAR(32), and a longer one fails at the very end of the upgrade, after all
the DDL has run.
"""

from __future__ import annotations

from alembic import op

revision = "0026_admin_policies"
down_revision = "0025_grant_beacon_app"
branch_labels = None
depends_on = None

# "A genuine global admin." The SCOPE alone is not enough, and this was a real
# escalation before it was fixed: the app's permission map gives
# `Role.VIEWER` read access only, and a predicate testing `scope_kind =
# 'global'` without the role handed a global-scope viewer full
# administration. Probed as `beacon_app` -- it created a team, promoted itself
# to `beacon_admin`, and granted itself `team_admin` in an unrelated team.
#
# `routes/teams.py` requires exactly this pair, so the policy matches it.
IS_GLOBAL_ADMIN = """
        EXISTS (
            SELECT 1 FROM current_user_scopes() mine
            WHERE mine.scope_kind = 'global' AND mine.role = 'beacon_admin'
        )
"""

# "An admin of the scope this row belongs to, or a global admin." Written once
# and interpolated, because it appears in several places and several copies of
# a security predicate is how one of them drifts.
#
# `{table}` is the row being checked. `current_user_scopes()` returns the
# caller's own memberships -- see the module docstring for why it must be a
# function rather than a subquery on `memberships`.
ADMIN_OF_ROW = (
    """
        (EXISTS (
            SELECT 1 FROM current_user_scopes() mine
            WHERE mine.scope_kind = {table}.scope_kind
              AND mine.scope_id = {table}.scope_id
              AND mine.role IN ('team_admin', 'beacon_admin')
        )
        OR """
    + IS_GLOBAL_ADMIN
    + ")"
)

# `beacon_admin` is the role the permission map expands to EVERY permission,
# including GLOBAL_ADMIN -- and it does so at team scope as well as global. So
# writing that role is reserved to a genuine global admin. Without this a team
# admin could rewrite a membership in their own team to `beacon_admin` and
# collect every permission in the system; probed, that is exactly what
# happened.
WRITES_A_SAFE_ROLE = (
    """
        ({table}.role <> 'beacon_admin' OR """
    + IS_GLOBAL_ADMIN
    + ")"
)

# Nobody edits their OWN membership row. An admin who needs their role changed
# asks another admin, which is a small inconvenience against a large hole: the
# row an admin is most able to write is their own, and role escalation is the
# one write where being the subject is the point. `USING` sees the old row and
# `WITH CHECK` the new one, so both carry this.
NOT_MY_OWN_ROW = "{table}.user_id <> current_user_id()"

# "Shares any scope with the caller" -- the read predicate for `users`.
SHARES_A_SCOPE = """
        EXISTS (
            SELECT 1 FROM current_user_scopes() mine
            JOIN memberships theirs
              ON theirs.scope_kind = mine.scope_kind AND theirs.scope_id = mine.scope_id
            WHERE theirs.user_id = users.id
        )
"""


def upgrade() -> None:
    # ---------------------------------------------------------------- helper
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_user_scopes()
        RETURNS TABLE (scope_kind varchar(20), scope_id uuid, role varchar(40))
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT m.scope_kind, m.scope_id, m.role
            FROM memberships m
            WHERE m.user_id = current_user_id()
        $$;
        """
    )

    # `add_team_member` has to decide whether an email already has an account
    # before it creates one, and `users_read` cannot answer that: an invitee
    # who is not yet a co-member is invisible, so the route concluded "new",
    # called `create`, and hit the unique constraint on `users.email` -- a 500,
    # since only BeaconIamError and BeaconRegistryError have handlers. Probed
    # as a team admin.
    #
    # The alternative was widening `users_read` so any team admin could read
    # every user row, which is email and name enumeration across the whole
    # install. This is deliberately narrower: it answers ONE question about ONE
    # address and returns nothing else. A team admin can already learn the same
    # fact by attempting the invite and seeing what happens, so it discloses
    # nothing the feature does not.
    #
    # It returns `is_active` as well as the id, because the route distinguishes
    # "no account" from "account exists but is deactivated" -- a 409. Returning
    # the id alone left the caller unable to answer that without a read the
    # policy refuses, so a first attempt at this handed back a uuid the caller
    # then threw away by re-reading the row through RLS.
    #
    # Restricted to callers who administer something, so it does not let every
    # authenticated user test arbitrary addresses for an account.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION user_for_invite(p_email text)
        RETURNS TABLE (id uuid, is_active boolean)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT u.id, u.is_active FROM users u
            WHERE u.email = p_email
              AND EXISTS (
                  SELECT 1 FROM memberships m
                  WHERE m.user_id = current_user_id()
                    AND (m.role IN ('team_admin', 'beacon_admin'))
              )
        $$;
        """
    )

    # ----------------------------------------------------------- memberships
    op.execute("DROP POLICY IF EXISTS memberships_self ON memberships;")
    # READ: your own rows, plus the roster of any scope you belong to. The
    # roster is what add/remove/issue-key all check membership through.
    # The global-admin branch is load-bearing and was missing. Postgres applies
    # the SELECT policy when a DELETE's WHERE reads the relation, so without it
    # `delete_team`'s membership purge matched nothing while the team row itself
    # was deletable -- probed as a global admin who is NOT a member of the team:
    # 0 memberships deleted, 1 team deleted, and `Membership.scope_id` carries
    # no foreign key to `teams`, so the grants outlived their team and the route
    # answered 204. An earlier probe missed it because the actor held a team
    # membership as well, which the scope branch covered.
    op.execute(
        f"""
        CREATE POLICY memberships_read ON memberships FOR SELECT
        USING (
            current_user_id() IS NULL
            OR user_id = current_user_id()
            OR EXISTS (
                SELECT 1 FROM current_user_scopes() mine
                WHERE mine.scope_kind = memberships.scope_kind
                  AND mine.scope_id = memberships.scope_id
            )
            OR {IS_GLOBAL_ADMIN}
        );
        """
    )
    admin = ADMIN_OF_ROW.format(table="memberships")
    safe_role = WRITES_A_SAFE_ROLE.format(table="memberships")
    not_self = NOT_MY_OWN_ROW.format(table="memberships")

    # INSERT takes only a WITH CHECK -- there is no existing row to test. Self
    # is NOT excluded here: `TeamService.create` grants the creator their first
    # membership in a brand-new team, and the scope has no other admin to ask.
    # Escalation by insert is closed by the primary key instead -- you cannot
    # insert a second row for a scope you are already in.
    op.execute(
        f"CREATE POLICY memberships_insert ON memberships FOR INSERT "
        f"WITH CHECK (current_user_id() IS NULL OR ({admin} AND {safe_role}));"
    )
    # UPDATE takes BOTH: USING decides which rows it may touch, WITH CHECK what
    # it may leave behind. Omitting the second would let an admin move a
    # membership into a scope they do not administer.
    op.execute(
        f"CREATE POLICY memberships_update ON memberships FOR UPDATE "
        f"USING (current_user_id() IS NULL OR ({admin} AND {not_self})) "
        f"WITH CHECK (current_user_id() IS NULL OR "
        f"({admin} AND {not_self} AND {safe_role}));"
    )
    op.execute(
        f"""
        CREATE POLICY memberships_delete ON memberships FOR DELETE
        USING (current_user_id() IS NULL OR {admin});
        """
    )

    # ----------------------------------------------------------------- users
    op.execute("DROP POLICY IF EXISTS users_self ON users;")
    op.execute(
        f"""
        CREATE POLICY users_read ON users FOR SELECT
        USING (
            current_user_id() IS NULL
            OR id = current_user_id()
            OR {SHARES_A_SCOPE}
            OR {IS_GLOBAL_ADMIN}
        );
        """
    )
    # INSERT: an invite. `add_team_member` creates the account for an email
    # nobody has yet, so the writer is an admin somewhere rather than the
    # subject. Not `true`: that would let any authenticated caller create
    # accounts, which is broader than the API's own TEAM_MANAGE gate.
    op.execute(
        """
        CREATE POLICY users_insert ON users FOR INSERT
        WITH CHECK (
            current_user_id() IS NULL
            OR id = current_user_id()
            OR EXISTS (
                SELECT 1 FROM current_user_scopes() mine
                WHERE mine.role IN ('team_admin', 'beacon_admin')
            )
        );
        """
    )
    # UPDATE and DELETE stay SELF, plus a global admin. Sharing a team with
    # someone lets you see them, not rename or remove them.
    for command, name in (("UPDATE", "users_update"), ("DELETE", "users_delete")):
        extra = (
            f"WITH CHECK (current_user_id() IS NULL OR id = current_user_id() OR {IS_GLOBAL_ADMIN})"
            if command == "UPDATE"
            else ""
        )
        op.execute(
            f"""
            CREATE POLICY {name} ON users FOR {command}
            USING (
                current_user_id() IS NULL
                OR id = current_user_id()
                OR {IS_GLOBAL_ADMIN}
            )
            {extra};
            """
        )

    # ----------------------------------------------------------------- teams
    # The read keeps its membership predicate and gains the global admin, so
    # `list_teams`' admin branch returns what its docstring promises.
    op.execute("DROP POLICY IF EXISTS teams_visible ON teams;")
    op.execute(
        f"""
        CREATE POLICY teams_read ON teams FOR SELECT
        USING (
            current_user_id() IS NULL
            OR EXISTS (
                SELECT 1 FROM current_user_scopes() mine
                WHERE mine.scope_kind = 'team' AND mine.scope_id = teams.id
            )
            OR {IS_GLOBAL_ADMIN}
        );
        """
    )
    # INSERT: a global admin, matching `TeamService.create`, which refuses
    # without GLOBAL_ADMIN. A team has no scope of its own to check against --
    # it IS the scope -- and it has no membership yet, which is exactly what
    # made the reused USING predicate refuse it.
    op.execute(
        f"""
        CREATE POLICY teams_insert ON teams FOR INSERT
        WITH CHECK (current_user_id() IS NULL OR {IS_GLOBAL_ADMIN});
        """
    )
    for command, name in (("UPDATE", "teams_update"), ("DELETE", "teams_delete")):
        admin_of_team = f"""
            EXISTS (
                SELECT 1 FROM current_user_scopes() mine
                WHERE mine.scope_kind = 'team' AND mine.scope_id = teams.id
                  AND mine.role IN ('team_admin', 'beacon_admin')
            )
            OR {IS_GLOBAL_ADMIN}
        """
        extra = (
            f"WITH CHECK (current_user_id() IS NULL OR {admin_of_team})"
            if command == "UPDATE"
            else ""
        )
        op.execute(
            f"""
            CREATE POLICY {name} ON teams FOR {command}
            USING (current_user_id() IS NULL OR {admin_of_team})
            {extra};
            """
        )

    # -------------------------------------------------------------- api_keys
    op.execute("DROP POLICY IF EXISTS api_keys_self ON api_keys;")
    # READ stays yours alone. A key hash is not something a co-member needs,
    # and `issue_member_key` returns the plaintext it just generated rather
    # than reading one back.
    op.execute(
        """
        CREATE POLICY api_keys_read ON api_keys FOR SELECT
        USING (current_user_id() IS NULL OR user_id = current_user_id());
        """
    )
    # INSERT: your own, or one for a member of a team you administer. The
    # second is impersonation by design -- see the module docstring -- and the
    # policy matches the API rather than differing from it in either
    # direction.
    op.execute(
        """
        CREATE POLICY api_keys_insert ON api_keys FOR INSERT
        WITH CHECK (
            current_user_id() IS NULL
            OR user_id = current_user_id()
            OR EXISTS (
                SELECT 1 FROM current_user_scopes() mine
                JOIN memberships target
                  ON target.scope_kind = mine.scope_kind AND target.scope_id = mine.scope_id
                WHERE target.user_id = api_keys.user_id
                  AND (mine.role IN ('team_admin', 'beacon_admin') OR mine.scope_kind = 'global')
            )
        );
        """
    )
    # UPDATE covers `touch` on every authenticated request, so it must stay
    # open to the key's owner; DELETE is revocation, likewise.
    for command, name in (("UPDATE", "api_keys_update"), ("DELETE", "api_keys_delete")):
        extra = (
            "WITH CHECK (current_user_id() IS NULL OR user_id = current_user_id())"
            if command == "UPDATE"
            else ""
        )
        op.execute(
            f"""
            CREATE POLICY {name} ON api_keys FOR {command}
            USING (current_user_id() IS NULL OR user_id = current_user_id())
            {extra};
            """
        )

    op.execute("GRANT EXECUTE ON FUNCTION current_user_scopes() TO beacon_app")
    op.execute("GRANT EXECUTE ON FUNCTION user_for_invite(text) TO beacon_app")


def downgrade() -> None:
    for table, names in (
        ("memberships", ("read", "insert", "update", "delete")),
        ("users", ("read", "insert", "update", "delete")),
        ("teams", ("read", "insert", "update", "delete")),
        ("api_keys", ("read", "insert", "update", "delete")),
    ):
        for suffix in names:
            op.execute(f"DROP POLICY IF EXISTS {table}_{suffix} ON {table};")

    op.execute(
        """
        CREATE POLICY memberships_self ON memberships FOR ALL
        USING (current_user_id() IS NULL OR user_id = current_user_id());
        """
    )
    op.execute(
        """
        CREATE POLICY users_self ON users FOR ALL
        USING (current_user_id() IS NULL OR id = current_user_id());
        """
    )
    op.execute(
        """
        CREATE POLICY teams_visible ON teams FOR ALL
        USING (
            current_user_id() IS NULL
            OR EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = current_user_id()
                  AND m.scope_kind = 'team'
                  AND m.scope_id = teams.id
            )
        );
        """
    )
    op.execute(
        """
        CREATE POLICY api_keys_self ON api_keys FOR ALL
        USING (current_user_id() IS NULL OR user_id = current_user_id());
        """
    )
    op.execute("DROP FUNCTION IF EXISTS user_for_invite(text);")
    op.execute("DROP FUNCTION IF EXISTS current_user_scopes();")
