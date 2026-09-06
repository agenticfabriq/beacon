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

These policies are still INERT where this is deployed: the app connects as the
cluster owner, which never consults one. 0025 grants a constrained
``beacon_app`` so that can change, but the serving DSN is not switched yet --
``test_a_constrained_role_cannot_insert_a_team`` records why.
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
    saying so matters: ``memberships`` is itself under FORCE RLS, so the
    subquery only ever sees the caller's own rows. A maintainer who deletes
    that clause and sees green should conclude it is redundant defence in depth
    -- which it is -- and not that this test is broken.
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


def test_the_policies_are_still_inert_where_this_is_deployed(engine: Engine) -> None:
    """The app's configured role bypasses RLS, so no policy here decides anything.

    Kept because the gap is still open, and it is open for a reason that was
    discovered by trying to close it. 0025 grants a constrained ``beacon_app``,
    which is the schema half. Pointing the serving DSN at it does NOT work
    yet: see ``test_a_constrained_role_cannot_insert_a_team`` below.

    Reads the DECLARED app DSN from the Makefile, then that role's privileges.
    Both halves are needed and each was wrong once -- reading privileges off
    the TEST connection cannot see the app's role, and reading the name alone
    stays green when the role is constrained in place.

    This FAILS once the serving DSN names a constrained role, which is the
    signal to delete it and to correct the notes in 0024 and the Makefile.
    """
    makefile = (pathlib.Path(__file__).parents[4] / "Makefile").read_text()
    declared = [line for line in makefile.splitlines() if line.startswith("DATABASE_URL ?=")]
    assert len(declared) == 1, (
        f"expected exactly one app DATABASE_URL default in the Makefile, found {declared}"
    )
    match = re.search(r"://([^:@/]+)", declared[0])
    assert match, f"could not read a role out of {declared[0]!r}"
    role = match.group(1)

    with engine.connect() as conn:
        attrs = conn.execute(
            sa.text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"),
            {"r": role},
        ).one_or_none()

    assert attrs is not None, (
        f"the app's configured role {role!r} does not exist in this database, so "
        "whether it bypasses RLS cannot be read here"
    )
    assert bool(attrs[0] or attrs[1]), (
        f"the app is now configured as {role!r}, which does not bypass RLS "
        f"(rolsuper={attrs[0]}, rolbypassrls={attrs[1]}). If the write paths were "
        "fixed too, delete this test and update 0024 and the Makefile. Do not widen "
        "the assertion to make it pass."
    )


def test_a_constrained_role_cannot_insert_a_team(engine: Engine) -> None:
    """Why the serving DSN is not switched: RLS refuses the writes.

    Every tenant policy is ``FOR ALL USING (...)`` with no ``WITH CHECK``, and
    Postgres reuses USING as the INSERT check. So under a constrained role a
    write must satisfy the same membership predicate a read does -- and a
    brand-new team has no membership, so its creator cannot insert it.

    ``TeamService.create`` inserts the team BEFORE granting the creator's
    membership, so ``POST /v1/teams`` would 500. ``add_team_member`` inserts a
    ``users`` row and a ``memberships`` row for someone OTHER than the actor,
    so both fail. This is the precondition for the switch, recorded as a test
    so it cannot be rediscovered the hard way.

    It passes while the gap exists. Once ``WITH CHECK`` clauses are added it
    will fail, which is the point: that is when the switch becomes safe.
    """
    factory = make_session_factory(engine)
    with factory() as session:
        user = UserRepo(session).create(email="wc-probe@example.com", name="W")
        team = TeamRepo(session).create(name="wc-probe-team")
        session.flush()
        MembershipRepo(session).grant(
            user_id=user.id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team.id,
            role=Role.TEAM_ADMIN,
        )
        session.commit()
        uid = user.id

    with factory() as session:
        session.execute(text("SET LOCAL ROLE beacon_app"))
        set_current_user(session, uid)
        with pytest.raises(ProgrammingError) as caught:
            session.execute(
                text(
                    "INSERT INTO teams (id, name, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'wc-probe-2', now(), now())"
                )
            )
        assert "row-level security" in str(caught.value), (
            "expected the insert to be refused BY THE POLICY; a different failure "
            f"means this test is measuring something else: {caught.value}"
        )
        session.rollback()


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
