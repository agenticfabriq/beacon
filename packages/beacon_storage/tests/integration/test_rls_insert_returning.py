"""An ORM insert must not be refused by the SELECT policy it never meant to consult.

SQLAlchemy flushes ``INSERT ... RETURNING <server-default columns>`` so the
mapper can read back what the database filled in. Postgres applies SELECT
policies to a RETURNING row. So on a table whose SELECT policy is narrower than
its INSERT policy, a row the INSERT policy PERMITS can still fail -- the write
is legal and reading it back is not.

That is invisible three ways over, which is why it needs a test rather than
care. It cannot happen to the role the tests use, because that role is a
SUPERUSER and a superuser consults no policy -- not because it owns the tables,
which would not save it: every tenancy table sets FORCE ROW LEVEL SECURITY,
and that makes policies apply to the owner precisely. So the whole suite is
green until the serving DSN switches to a non-superuser. It cannot be seen in
either policy alone, because each is correct by itself. And when it does
happen, the message gives no hint which policy refused. Captured verbatim, as a
team admin inviting a new user -- the plain INSERT accepted, the same statement
with RETURNING refused:

    new row violates row-level security policy for table "users"

Byte-identical to what an INSERT-policy refusal prints, with no ``(USING
expression)`` qualifier. Postgres does emit that variant, but for the
``ON CONFLICT DO UPDATE`` conflict check, not for a RETURNING row -- so an
operator who reads the message and goes to audit the INSERT policy is looking
at the one that permitted the write.

Measured on both victims -- the same statement was ACCEPTED plain and REFUSED
with RETURNING:

* ``api_keys`` -- an admin issuing a member's first credential, where
  ``api_keys_read`` is deliberately restricted to the key's owner. Fixing it by
  widening read would have traded away a real invariant to work around an ORM
  detail, so ``ApiKey`` sets ``eager_defaults`` instead.
* ``users`` -- inviting a NEW person, where ``users_read`` cannot see an account
  nobody is yet a co-member of. Inviting an EXISTING user worked, which is why
  the suite missed it: the invite test used an account that already existed.

Both were found by accident, one week apart, which is the argument for sweeping
rather than waiting for the third.

**What this test does and does not decide.** Whether a SELECT policy is narrower
than an INSERT policy is not decidable from the expressions, so the flag here is
a cheap over-approximation: the mapper emits RETURNING, and the two predicates
are not textually identical. That over-approximates in the safe direction and
catches both known victims. Every table it flags is then settled by PROBE, and
recorded below with what the probe found -- a flagged table is not a defect, an
unexplained one is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from beacon_storage.models.base import Base
from sqlalchemy import Table, text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

# Flagged by the rule above, and settled by probing each one as a realistic
# constrained actor. The reason has to say what was OBSERVED, not what is
# expected -- an entry reading "should be fine" is how a real victim gets
# waved through.
EXPLAINED: dict[str, str] = {
    "memberships": (
        "An admin granting a membership to someone else. `memberships_read` admits the "
        "inserter through its shared-scope branch -- the admin belongs to the very scope "
        "the new row names -- so the row is visible the moment it exists. Probed as a "
        "team_admin granting into their own team: plain ACCEPTED, RETURNING ACCEPTED."
    ),
    "teams": (
        "Not reachable, because the INSERT refuses first. `teams_insert` requires a GLOBAL "
        "beacon_admin, and `teams_read` admits global beacon_admins too -- so the only "
        "actor who can insert can also read back. Probed as an ordinary authenticated "
        "user: REFUSED plain, before RETURNING is reached. That refusal is its own "
        "question, tracked with the DSN switch, but it is not this trap."
    ),
}


def _emits_returning() -> dict[str, tuple[str, object, bool]]:
    """Per table: the mapper's name, its ``eager_defaults``, and whether it RETURNs.

    A mapper reads server defaults back unless ``eager_defaults`` is explicitly
    False, so a table with server-side columns and any other setting emits
    RETURNING on insert.
    """
    import beacon_storage.models  # noqa: F401,PLC0415  (populate the registry)

    out: dict[str, tuple[str, object, bool]] = {}
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if not isinstance(table, Table):
            continue
        has_server_default = any(c.server_default is not None for c in table.columns)
        out[table.name] = (
            mapper.class_.__name__,
            mapper.eager_defaults,
            has_server_default and mapper.eager_defaults is not False,
        )
    return out


def test_every_rls_table_that_could_be_bitten_is_accounted_for(engine: Engine) -> None:
    models = _emits_returning()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
            SELECT c.relname AS tbl,
                   max(CASE WHEN p.polcmd = 'a'
                            THEN pg_get_expr(p.polwithcheck, p.polrelid) END) AS ins,
                   max(CASE WHEN p.polcmd = 'r'
                            THEN pg_get_expr(p.polqual, p.polrelid) END) AS sel,
                   max(CASE WHEN p.polcmd = '*'
                            THEN pg_get_expr(p.polqual, p.polrelid) END) AS both
            FROM pg_class c
            JOIN pg_policy p ON p.polrelid = c.oid
            WHERE c.relrowsecurity
            GROUP BY c.relname
            """)
        ).all()

    assert rows, (
        "no table reports RLS enabled, so this test is inspecting an empty set and would "
        "pass against a schema with every policy dropped"
    )

    flagged: list[str] = []
    for row in rows:
        _, _, returning = models.get(row.tbl, ("", None, False))
        if not returning:
            continue
        # A `FOR ALL` policy supplies both sides from one expression, so it can
        # never be narrower on one than the other.
        insert_check = row.ins if row.ins is not None else row.both
        select_using = row.sel if row.sel is not None else row.both

        if insert_check is None:
            # No INSERT policy on an RLS table denies every insert outright,
            # RETURNING or not. A louder failure than this trap and not this
            # one -- but skipped deliberately rather than by falling through.
            continue

        if select_using is None:
            # NOTHING is selectable, so a RETURNING insert cannot succeed even
            # where the INSERT policy permits the row. That is the worst case
            # of this trap, not an absent one, and an earlier version of this
            # loop skipped it with the same `continue` that skips a missing
            # INSERT policy -- passing in silence over a table where every ORM
            # insert fails.
            flagged.append(row.tbl)
            continue

        if insert_check.strip() != select_using.strip():
            flagged.append(row.tbl)

    unexplained = sorted(set(flagged) - set(EXPLAINED))
    assert not unexplained, (
        f"{unexplained} have a mapper that flushes INSERT ... RETURNING and a SELECT policy "
        "that is not the same expression as their INSERT policy, so an insert the INSERT "
        "policy permits may still be refused when the row is read back -- and only under "
        "the serving role, never as the owner. Probe each as a realistic constrained "
        "actor, running the same statement with and without RETURNING. If it is refused "
        "only with RETURNING, set `eager_defaults` False on the model rather than widening "
        "the read policy. If it is fine, record HERE what the probe showed."
    )

    stale = sorted(set(EXPLAINED) - set(flagged))
    assert not stale, (
        f"{stale} are explained here but no longer flagged, so the note is describing a "
        "shape the schema no longer has. Remove them: a stale entry silently pre-approves "
        "a table if it ever becomes risky again, which is the one thing this list must "
        "never do."
    )
