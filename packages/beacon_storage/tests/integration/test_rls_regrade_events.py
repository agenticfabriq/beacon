"""``regrade_events`` must not be readable across teams.

0024 added the tenant column and the policy, and shipped with neither of the
two checks ``result_outcomes`` has: no predicate assertion and no behavioural
one. Measured at the time: rewriting the policy to ``USING (true)`` left every
test in that change green. A guard whose passing value is indistinguishable
from its failure is the defect this suite keeps finding, so it gets both here.

Read ``test_the_policy_is_not_the_isolation_where_this_is_deployed`` before
concluding that a green run here means the deployed API is isolated. It does
not, and that test says why.
"""

from __future__ import annotations

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


def test_the_policy_is_not_the_isolation_where_this_is_deployed(engine: Engine) -> None:
    """The role the API actually connects as bypasses this policy entirely.

    This is the uncomfortable half, and it is asserted rather than left in a
    commit message because 0024's docstring presents the policy as the
    precondition for serving the table -- which overstates what it does in the
    configuration this repo ships.

    Measured: ``DATABASE_URL`` in ``Makefile`` and ``docker-compose.yml`` is
    the ``beacon`` role, which is the cluster owner and carries both
    ``rolsuper`` and ``rolbypassrls``. ``beacon_app`` -- the role the test
    above drops to, and the only one the policy can constrain -- is granted
    nothing by any migration and exists only in these fixtures.

    So for the History route, and for every other RLS table, the isolation
    that actually holds today is ``require_permission`` in the API layer. The
    policy is real defence in depth the moment the API connects as a
    non-superuser, and it should; until then a reader must not mistake a green
    run above for a claim about production.

    This test passes when the gap exists. It FAILS once ``beacon_app`` gains
    the grants and the app switches to it -- at which point delete it and
    update 0024's docstring, because the thing it documents will have been
    fixed.
    """
    with engine.connect() as conn:
        deployed_role = conn.execute(sa.text("SELECT current_user")).scalar_one()
        attrs = conn.execute(
            sa.text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
        ).one()

    bypasses = bool(attrs[0] or attrs[1])
    assert bypasses, (
        f"the connection role {deployed_role!r} no longer bypasses RLS. "
        "If the API now connects as a constrained role, this test has served "
        "its purpose: delete it and correct 0024's docstring, which currently "
        "warns that the policy is not load-bearing."
    )
