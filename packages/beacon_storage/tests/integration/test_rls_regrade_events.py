"""``regrade_events`` must not be readable across teams.

0024 added the tenant column and the policy, and shipped with neither of the
two checks ``result_outcomes`` has: no predicate assertion and no behavioural
one. Measured at the time: rewriting the policy to ``USING (true)`` left every
test in that change green. A guard whose passing value is indistinguishable
from its failure is the defect this suite keeps finding, so it gets both here: a
behavioural check under a role RLS applies to, and a predicate check that reads
this table's policy AND ``memberships``', which its isolation is scoped through.

Read ``test_the_policy_is_not_the_isolation_where_this_is_deployed`` before
concluding that a green run here means the deployed API is isolated. It does
not, and that test says why.
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


def test_the_policy_is_not_the_isolation_where_this_is_deployed() -> None:
    """The role the API is configured to connect as bypasses this policy.

    0024's comment presents the tenant column and policy as the precondition
    for serving this table. They are the precondition, and they are not by
    themselves sufficient, because the app connects as the cluster owner.

    **This reads the DECLARED app DSN, not this test's connection.** The first
    version asserted on ``current_user`` of the test engine, which cannot fail
    the way the docstring promised: ``make test`` points ``DATABASE_URL`` at a
    separate ``TEST_DATABASE_URL`` literal, so closing the gap -- changing the
    app's DSN to ``beacon_app`` -- would have left it green and 0024 would have
    gone on warning about a gap that was shut. That is the same
    check-that-cannot-fail this file was added to remove, so it is worth
    naming: the subject of the claim is the shipped configuration, and the
    assertion has to read the shipped configuration.

    ``beacon_app`` -- the constrained role the behavioural test drops to -- is
    granted nothing by any migration and exists only in fixtures.

    This test passes while the gap exists and FAILS once the app's DSN names a
    constrained role. At that point delete it and drop the warning from 0024,
    because the thing it documents will have been fixed.
    """
    makefile = (pathlib.Path(__file__).parents[4] / "Makefile").read_text()
    declared = [line for line in makefile.splitlines() if line.startswith("DATABASE_URL ?=")]
    assert len(declared) == 1, (
        f"expected exactly one app DATABASE_URL default in the Makefile, found {declared}"
    )
    role = re.search(r"://([^:@/]+)", declared[0])
    assert role, f"could not read a role out of {declared[0]!r}"

    assert role.group(1) == "beacon", (
        f"the app is now configured to connect as {role.group(1)!r}. If that role is "
        "constrained, this test has served its purpose: delete it and remove the "
        "warning from 0024, which says the policy is not load-bearing. Do not simply "
        "widen this assertion -- the warning in the migration is the thing that has to "
        "change."
    )


def test_the_policy_predicate_is_not_a_blanket_allow(engine: Engine) -> None:
    """The other check ``result_outcomes`` has, which 0024 also shipped without.

    The behavioural test above catches this table's own policy going to
    ``USING (true)``. It does NOT catch ``memberships``' policy being widened,
    because this table's isolation is scoped THROUGH that subquery: relax
    memberships and every membership row becomes visible to every caller, so
    the ``EXISTS`` here starts matching for teams the caller does not belong
    to -- without anyone editing 0024.

    So both predicates are read: this table's, and the one it depends on.
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
            )
        ).all()

    predicates: dict[str, str] = {str(row[0]): str(row[1]) for row in rows}
    for table in ("regrade_events", "memberships"):
        assert table in predicates, f"expected a USING predicate on {table}'s policy"
        expr = predicates[table]
        assert expr.strip().lower() != "true", (
            f"{table}'s policy must not be a blanket allow: regrade_events "
            "isolation is scoped through it"
        )
        assert "current_user_id()" in expr, f"{table} must stay user-scoped; got: {expr}"
    assert "team_id" in predicates["regrade_events"], (
        "regrade_events' policy must scope on its own tenant column; got: "
        f"{predicates['regrade_events']}"
    )
