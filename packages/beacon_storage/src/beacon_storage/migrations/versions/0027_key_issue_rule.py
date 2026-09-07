"""A team admin may not mint a credential carrying more privilege than its own.

``issue_member_key`` exists so an admin can hand an invited colleague their
first credential. The key authenticates as that colleague -- which is the
point, and also the hole: nothing checked that the colleague's privileges were
confined to the team the admin administers. Measured against the deployed app
before writing this, as a team admin holding no global role:

    step 1-2  add victim to my team      -> 201
    step 3    mint a key for the victim  -> 201
    step 4    authenticate with it       -> 200
              identity = <a global beacon_admin>   scopes = ['global', ...]

A global ``beacon_admin`` membership is counted by ``effective_permissions``
for EVERY team, so that key administers every tenant. The route is the live
half of the fix; this migration is the half that survives someone adding a
second caller.

**Why the old policy branch looked sufficient.** 0026's ``api_keys_insert``
permitted an insert when the target shared any scope the actor administered.
Sharing a scope is not the same as being confined to it: the victim shares my
team the moment I add them, while keeping everything they hold elsewhere.

**Why this is a rank comparison and not a scope test.** ``_ROLE_PERMISSIONS``
is a total order -- ``viewer < team_member < team_admin < beacon_admin`` --
verified by a test that fails if a role is ever added that is not comparable,
because THIS FUNCTION WOULD SILENTLY START LYING if it stopped being one.
Given the order, "the target's privileges are a subset of mine" is exactly
"at every scope the target holds, I hold at least their rank".

**Why a function and not an inline predicate.** The route has to apply the same
rule, and a route that reads ``memberships`` itself is bounded by
``memberships_read``: a membership of the target in a team the actor is not in
is INVISIBLE, so the check would pass on incomplete data and refuse nothing.
That is the failure mode this whole register keeps finding -- a guard whose
pass value is indistinguishable from being unable to look. SECURITY DEFINER
reads the full table, so route and policy answer from the same facts.

Both user ids are PARAMETERS rather than one being ``current_user_id()``, so
the route can ask about the actor it already resolved instead of depending on
the GUC being set on that code path. The disclosure is one bit -- whether one
user administers every scope another belongs to -- and reaching it requires
already knowing both ids.

Revision ID: 0027_key_issue_rule
Revises: 0026_admin_policies
Create Date: 2026-09-06 18:00:00
"""

from __future__ import annotations

from alembic import op

revision = "0027_key_issue_rule"
down_revision = "0026_admin_policies"
branch_labels = None
depends_on = None

APP_ROLE = "beacon_app"

# Kept in the same order as ``_ROLE_PERMISSIONS`` and asserted against it by
# ``test_the_sql_role_rank_matches_the_python_permission_lattice``. An unknown
# role returns NULL, which makes every comparison below NULL and therefore
# fails closed -- a role this function has not been taught about must not be
# quietly treated as the weakest one.
ROLE_RANK = """
CREATE OR REPLACE FUNCTION role_rank(p_role text)
RETURNS integer
LANGUAGE sql IMMUTABLE
AS $fn$
    SELECT CASE p_role
        WHEN 'viewer' THEN 1
        WHEN 'team_member' THEN 2
        WHEN 'team_admin' THEN 3
        WHEN 'beacon_admin' THEN 4
        ELSE NULL
    END
$fn$
"""

# `p_issuer` may issue a credential for `p_target` when either
#   - the issuer is a GLOBAL beacon_admin, who already holds everything a key
#     could carry, so no key can escalate them; or
#   - the target holds at least one membership, and every membership it holds
#     sits at a scope where the issuer holds an ADMIN role of at least the
#     target's rank.
#
# The `EXISTS` is not redundant with the `NOT EXISTS`. Over a target with no
# memberships at all, `NOT EXISTS` is vacuously true, so without it the
# function would return true for any user id -- including one that does not
# exist. Such a key carries no privilege today, but "permitted because there
# was nothing to check" is the shape of the next hole, not a safe default.
MAY_ISSUE = """
CREATE OR REPLACE FUNCTION may_issue_key_for(p_issuer uuid, p_target uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
AS $fn$
    SELECT
        EXISTS (
            SELECT 1 FROM memberships mine
            WHERE mine.user_id = p_issuer
              AND mine.scope_kind = 'global'
              AND mine.role = 'beacon_admin'
        )
        OR (
            EXISTS (SELECT 1 FROM memberships t WHERE t.user_id = p_target)
            AND NOT EXISTS (
                SELECT 1 FROM memberships t
                WHERE t.user_id = p_target
                  AND NOT EXISTS (
                      SELECT 1 FROM memberships mine
                      WHERE mine.user_id = p_issuer
                        AND mine.scope_kind = t.scope_kind
                        AND mine.scope_id = t.scope_id
                        AND mine.role IN ('team_admin', 'beacon_admin')
                        AND role_rank(mine.role) >= role_rank(t.role)
                  )
            )
        )
$fn$
"""

# The fourth branch is the only one that changes: `user_id = current_user_id()`
# still covers minting your own key, and the NULL branch still covers the
# pre-authentication paths.
INSERT_CHECK = """
CREATE POLICY api_keys_insert ON api_keys FOR INSERT
WITH CHECK (
    current_user_id() IS NULL
    OR user_id = current_user_id()
    OR may_issue_key_for(current_user_id(), user_id)
)
"""

# Read is deliberately NOT widened. An admin permitted to MINT a key for a
# member still cannot read that member's key rows, which is the invariant
# `test_api_key_hashes_stay_private_to_their_owner` exists for.
#
# That invariant and this policy collided once, through the ORM rather than
# through either of them: SQLAlchemy flushed `INSERT ... RETURNING created_at,
# updated_at` for the server-side timestamp defaults, Postgres applies SELECT
# policies to a RETURNING row, and so the admin branch of `api_keys_insert`
# could never succeed -- a permit that cannot be exercised, which is as much a
# defect as a check that cannot fail, and one that would have surfaced only
# when the serving DSN was switched. Measured: the same insert was ACCEPTED
# plain and REFUSED with RETURNING.
#
# Fixed on the ORM side (`ApiKey.__mapper_args__`), not here, so that reading a
# member's key hash stays impossible rather than becoming merely unnecessary.

OLD_INSERT_CHECK = """
CREATE POLICY api_keys_insert ON api_keys FOR INSERT
WITH CHECK (
    current_user_id() IS NULL
    OR user_id = current_user_id()
    OR EXISTS (
        SELECT 1 FROM current_user_scopes() mine
        WHERE mine.scope_kind = 'global' AND mine.role = 'beacon_admin'
    )
    OR EXISTS (
        SELECT 1 FROM current_user_scopes() mine
        JOIN memberships target
          ON target.scope_kind = mine.scope_kind AND target.scope_id = mine.scope_id
        WHERE target.user_id = api_keys.user_id
          AND mine.role IN ('team_admin', 'beacon_admin')
    )
)
"""


def upgrade() -> None:
    op.execute(ROLE_RANK)
    op.execute(MAY_ISSUE)
    # EXECUTE on a SECURITY DEFINER function is granted narrowly, to the
    # serving role only. PUBLIC would hand the disclosure to every role in the
    # cluster, including ones added later for unrelated reasons.
    op.execute("REVOKE ALL ON FUNCTION role_rank(text) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION may_issue_key_for(uuid, uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION role_rank(text) TO {APP_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION may_issue_key_for(uuid, uuid) TO {APP_ROLE}")

    op.execute("DROP POLICY IF EXISTS api_keys_insert ON api_keys")
    op.execute(INSERT_CHECK)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS api_keys_insert ON api_keys")
    op.execute(OLD_INSERT_CHECK)
    # Dropped AFTER the policies that call them, or the drop fails on the
    # dependency.
    op.execute("DROP FUNCTION IF EXISTS may_issue_key_for(uuid, uuid)")
    op.execute("DROP FUNCTION IF EXISTS role_rank(text)")
