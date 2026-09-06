"""``regrade_events`` must not be readable across teams.

0024 added the tenant column and the policy, and shipped with neither of the
two checks ``result_outcomes`` has: no predicate assertion and no behavioural
one. Measured at the time: rewriting the policy to ``USING (true)`` left every
test in that change green. A guard whose passing value is indistinguishable
from its failure is the defect this suite keeps finding, so it gets both here: a
behavioural check under a role RLS applies to, and a predicate check that reads
this table's policy and ``memberships``'. Note ``memberships`` is NOT what
isolates this table today -- the policy scopes on ``current_user_id()``
directly -- and the predicate test explains why it is still read.

These policies are live: 0025 grants a constrained ``beacon_app``, 0026
rewrote the identity-table policies so administration works under one, and the
serving DSN names it. ``test_the_serving_role_does_not_bypass_rls`` keeps it
that way, and ``test_rls_admin_policies.py`` covers what administration may
and may not do.
"""

from __future__ import annotations

import pathlib
import re
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from beacon_storage.db import make_session_factory
from beacon_storage.models.regrade_events import RegradeEvent
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.rls import set_current_user
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _use_app_role(session: Session) -> None:
    """Drop to the non-superuser role, or the policy is never consulted.

    Without this the connection is the cluster owner, which carries
    ``rolbypassrls``, and every assertion below would pass against a policy
    that denies nothing.
    """
    session.execute(text("SET LOCAL ROLE beacon_app"))


def _seed_team_with_event(session: Session, *, who: str) -> tuple[UUID, UUID]:
    """One team, one member, one recorded regrade. Returns (user_id, team_id)."""
    user = UserRepo(session).create(email=f"{who}@example.com", name=who)
    team = TeamRepo(session).create(name=f"{who}-regrade-team")
    session.flush()
    # The policy is membership-based, so a team without one sees nothing.
    MembershipRepo(session).grant(
        user_id=user.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=team.id,
        role=Role.TEAM_ADMIN,
    )
    session.add(
        RegradeEvent(
            team_id=team.id,
            suite_id=None,
            suite_name=f"{who}_suite",
            grader="result_set_match",
            grader_version="v8",
            headline_metric="exact_match",
            n_runs=1,
            n_graded=10,
            n_flipped=0,
            outcome_changes=[],
            reason=f"{who}'s regrade",
        )
    )
    session.flush()
    return user.id, team.id


def test_the_policy_actually_isolates_teams(engine: Engine) -> None:
    """Behavioural, not textual: the rows are queried as each user.

    What this catches is a policy that stops isolating -- ``USING (true)``, or
    one scoped on the wrong column -- which "a policy exists" and a substring
    check both pass.

    What it does NOT catch is deleting ``m.user_id = current_user_id()``, and
    the reason changed with 0026. It used to be that ``memberships`` showed
    only the caller's own rows; ``memberships_read`` now exposes the roster of
    any scope the caller belongs to. The clause is still redundant, because
    those rows are confined to the caller's own scopes -- so a maintainer who
    deletes it and sees green should conclude that, not that this test is
    broken. It stays so the reasoning lives in this table's own text.
    """
    factory = make_session_factory(engine)

    with factory() as setup:
        setup.execute(sa.text("DELETE FROM regrade_events"))
        alice_id, _ = _seed_team_with_event(setup, who="rls-alice")
        bob_id, _ = _seed_team_with_event(setup, who="rls-bob")
        setup.commit()

    with factory() as session:
        _use_app_role(session)
        set_current_user(session, alice_id)
        assert len(list(session.scalars(sa.select(RegradeEvent)))) == 1, (
            "a member sees their own team's regrade history"
        )

    with factory() as session:
        _use_app_role(session)
        set_current_user(session, bob_id)
        assert len(list(session.scalars(sa.select(RegradeEvent)))) == 1, (
            "and exactly one -- not alice's as well"
        )

    with factory() as session:
        _use_app_role(session)
        nobody = UserRepo(session).create(email="rls-nobody@example.com", name="N")
        session.flush()
        set_current_user(session, nobody.id)
        assert list(session.scalars(sa.select(RegradeEvent))) == [], (
            "a user with no membership anywhere sees nothing"
        )


def test_the_serving_role_does_not_bypass_rls(engine: Engine) -> None:
    """The app's configured role must be constrained, or no policy decides anything.

    This replaces a test that asserted the OPPOSITE, twice over. The first
    version kept a known gap visible: the app connected as the cluster owner,
    whose queries never consult a policy. 0025 granted a constrained role and
    0026 rewrote the tenant policies so administration works under one, so the
    gap is shut and this is the inverse -- it fails if the DSN is ever pointed
    back at a role that bypasses.

    Reads every DECLARED serving DSN -- the Makefile, ``.env.example`` and the
    README's launch commands -- and then each named role's privileges. Both
    halves are needed and each was wrong once: privileges read off the TEST
    connection cannot see the app's role, and a name alone stays green when a
    role is constrained, or unconstrained, in place. Reading only the Makefile
    was a third gap: the README and ``.env.example`` still named the owner
    after the switch, so the documented path left the policies inert.
    """
    root = pathlib.Path(__file__).parents[4]
    # EVERY place an operator gets a DSN, not just the Makefile. `.env.example`
    # is what the README says to copy to `.env`, and `ApiConfig` loads that --
    # so a guard reading only the Makefile leaves the documented path free to
    # point at the owning role with every policy inert, which is the condition
    # this whole thread was about.
    declared: dict[str, str] = {}
    owning: dict[str, str] = {}
    for line in (root / "Makefile").read_text().splitlines():
        if line.startswith("DATABASE_URL ?="):
            declared["Makefile"] = line
    for line in (root / ".env.example").read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            declared[".env.example"] = line
    # Any line naming a postgres DSN, not just `DATABASE_URL=...`. The labelled
    # serving/migrating block reads `serving:  postgresql+psycopg://...` with no
    # assignment, so an assignment-only filter skipped the FIRST and most
    # prominent DSN an operator sees -- changing that one line to the owning
    # role left every test green.
    #
    # Classified by whether the line MARKS itself as the migrating one, and
    # keyed by line number so a second such line cannot overwrite the first.
    # An earlier version keyed on a constant and matched only the literal
    # prefix `migrating:`, so documenting the owning DSN in the shape the
    # Makefile tells deployments to use -- `MIGRATE_DATABASE_URL=...` -- was
    # read as a SERVING declaration and failed with a message asserting the
    # opposite of what the line said.
    for number, line in enumerate((root / "README.md").read_text().splitlines(), 1):
        if "postgresql+psycopg://" not in line:
            continue
        marked = "migrat" in line.lower()
        where = f"README.md:{number}"
        (owning if marked else declared)[where] = line

    assert "Makefile" in declared and ".env.example" in declared, (
        f"expected a serving DSN in both the Makefile and .env.example: {sorted(declared)}"
    )

    roles = {}
    for where, line in declared.items():
        match = re.search(r"://([^:@/]+)", line)
        assert match, f"could not read a role out of {where}: {line!r}"
        roles[where] = match.group(1)

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = ANY(:names)"
            ),
            {"names": sorted(set(roles.values()))},
        ).all()
    attrs = {r[0]: (r[1], r[2]) for r in rows}

    for where, line in sorted(owning.items()):
        match = re.search(r"://([^:@/]+)", line)
        assert match, f"could not read a role out of {where}: {line!r}"
        assert match.group(1) == "beacon", (
            f"{where} is the MIGRATING DSN and must name the owning role, since "
            f"alembic runs DDL; it names {match.group(1)!r}"
        )

    for where, role in sorted(roles.items()):
        assert role in attrs, (
            f"{where} names role {role!r}, which does not exist in this database, so "
            "whether it bypasses RLS cannot be read here"
        )
        rolsuper, bypass = attrs[role]
        assert not rolsuper and not bypass, (
            f"{where} tells an operator to connect as {role!r}, which has "
            f"rolsuper={rolsuper} rolbypassrls={bypass}. A role with either attribute "
            "never consults a policy, so every policy in this schema would be inert "
            "and require_permission would be the only isolation."
        )


def test_the_serving_role_cannot_run_ddl(engine: Engine) -> None:
    """Serving and owning are different roles, and 0025 keeps them apart.

    0025 grants ``beacon_app`` DML and nothing else. If it could create or drop
    tables there would be little left of the separation -- and a compromised app
    could DISABLE the policies constraining it, which is a more direct route to
    the data than reading around them.

    Still meaningful with the serving DSN unswitched: it asserts what the
    grants do and do not confer, which is what the switch will rely on.
    """
    factory = make_session_factory(engine)
    with factory() as session:
        session.execute(text("SET LOCAL ROLE beacon_app"))
        with pytest.raises(ProgrammingError):
            session.execute(text("CREATE TABLE rls_probe_should_fail (id int)"))
        session.rollback()

    with factory() as session:
        session.execute(text("SET LOCAL ROLE beacon_app"))
        with pytest.raises(ProgrammingError):
            session.execute(text("ALTER TABLE regrade_events DISABLE ROW LEVEL SECURITY"))
        session.rollback()


def test_the_policy_predicate_is_not_a_blanket_allow(engine: Engine) -> None:
    """The other check ``result_outcomes`` has, which 0024 also shipped without.

    The behavioural test above catches this table's own policy going to
    ``USING (true)``. This one reads the predicates, and it reads
    ``memberships``' as well as this table's.

    **Why memberships, stated correctly.** Widening ``memberships`` alone does
    NOT leak this table: the policy's subquery carries
    ``m.user_id = current_user_id()`` explicitly, so the ``EXISTS`` keeps
    matching only the caller's own rows. An earlier version of this docstring
    said otherwise and contradicted the sibling test above, which has it right.

    The real break is TWO steps -- delete that clause as redundant, and widen
    ``memberships`` -- and each step is individually harmless, which is what
    makes the pair dangerous. Neither is visible from the other's diff. So
    memberships' predicate is asserted here to catch step two even though step
    one has not happened, and the sibling test names the coupling. A maintainer
    should conclude that the clause is redundant TODAY and that removing it
    makes this table depend on another table's policy -- not that memberships
    is currently load-bearing.

    **The strength of that assertion is limited, deliberately stated.** It
    rejects a literal ``true`` and a predicate that never mentions
    ``current_user_id()``. A realistic widening -- an admin-visibility clause
    OR'd into the existing one -- satisfies both and would pass. Catching that
    needs a behavioural test of the two-step break, which is not here.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT c.relname, pg_get_expr(p.polqual, p.polrelid) FROM pg_policy p"
                " JOIN pg_class c ON c.oid = p.polrelid"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'public'"
                " AND c.relname IN ('regrade_events', 'memberships')"
                " AND p.polqual IS NOT NULL"
                # PERMISSIVE only. Restrictive policies are AND'd, so a
                # restrictive `true` grants nothing and a restrictive policy
                # that never mentions current_user_id() can be perfectly
                # sound -- both would fail the assertions below with a message
                # claiming they open the table.
                " AND p.polpermissive"
            )
        ).all()

    # A LIST per table, not one entry. Permissive policies are OR'd, so adding
    # `USING (true)` alongside the real one opens the table while the real one
    # is still there to be found -- and a dict keyed on the table name keeps
    # whichever row the planner happened to return, `pg_policy` order being
    # unspecified. Every predicate is asserted.
    predicates: dict[str, list[str]] = {}
    for row in rows:
        predicates.setdefault(str(row[0]), []).append(str(row[1]))

    for table in ("regrade_events", "memberships"):
        exprs = predicates.get(table, [])
        assert exprs, f"expected a USING predicate on {table}'s policy"
        for expr in exprs:
            assert expr.strip().lower() != "true", (
                f"{table} has a blanket-allow policy: permissive policies are OR'd, "
                f"so this opens the table regardless of the others. Got: {exprs}"
            )
            assert "current_user_id()" in expr, (
                f"every {table} policy must stay user-scoped; got: {expr}"
            )
    assert all("team_id" in expr for expr in predicates["regrade_events"]), (
        "every regrade_events policy must scope on its own tenant column; got: "
        f"{predicates['regrade_events']}"
    )
