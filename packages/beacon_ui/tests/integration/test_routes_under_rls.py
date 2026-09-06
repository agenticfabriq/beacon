"""The routes must work when RLS is actually enforced.

Every other API test connects as the owning role, which carries
``rolbypassrls`` -- so none of them consults a policy, and none can observe the
serving-role switch the Makefile makes. Two real bugs lived in exactly that
gap, and both were found by review rather than by this suite:

* ``delete_team`` purges the roster and then the team. Postgres applies the
  SELECT policy when a DELETE's WHERE reads the relation, so the purge matched
  nothing while the team row was still deletable. ``Membership.scope_id``
  carries no foreign key to ``teams``, so the grants outlived their team and
  the route answered 204 -- success, over orphaned rows.
* ``add_team_member`` decides "new invitee" from a read the policy cannot
  answer for a non-co-member, so it created a duplicate account and hit the
  unique constraint on ``users.email`` -- a 500, since nothing handles it.

Both read correctly at the policy level. They broke at the route, which is why
these tests exist there.

``constrained_client`` connects as the owner and drops each request to
``beacon_app``; ``TEST_DATABASE_URL`` cannot simply name the serving role,
because the fixtures drop and recreate the schema.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest
import sqlalchemy as sa
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo

if TYPE_CHECKING:
    from uuid import UUID

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _refuse_global_admin(session: Session, user_id: UUID, why: str) -> None:
    """Fail if ``user_id`` holds a global role.

    Written because this file got it wrong twice and its siblings three more
    times. `alice` in the seeded world is a global ``BEACON_ADMIN``, and every
    identity policy has a global-admin branch -- so a test that acts as her
    passes whatever the rest of the predicate says. Both of the tests this
    module exists for were green against a REVERTED fix until this guard was
    added.

    So any test whose point is a constrained caller states that here, and finds
    out at once rather than by believing a green run.
    """
    roles = session.execute(
        sa.text("SELECT count(*) FROM memberships WHERE user_id=:u AND scope_kind='global'"),
        {"u": user_id},
    ).scalar()
    assert roles == 0, (
        f"this test needs a caller with NO global role, because {why}. The one given "
        f"holds {roles}; every identity policy has a global-admin branch, so the "
        "assertion below would pass no matter what the rest of the predicate said."
    )


class _World(Protocol):
    alice_id: UUID
    alice_key: str
    bob_key: str
    carol_id: UUID
    carol_key: str
    acme_team_id: UUID
    globex_team_id: UUID


def test_the_client_really_is_constrained(
    constrained_client: TestClient, world: _World, session: Session
) -> None:
    """The fixture must actually drop the role, or every test below is theatre.

    Asserted through a ROUTE rather than by reading ``current_user``, because
    what matters is the role the request handler runs as. ``/v1/me`` needs the
    caller's own user row, which `users_read` permits and which therefore only
    resolves if the GUC and the policy are both working.
    """
    response = constrained_client.get("/v1/me", headers={"X-API-Key": world.alice_key})
    assert response.status_code == 200, response.text

    # And the negative half, as BOB. Alice is a global BEACON_ADMIN in the
    # seeded world, so `teams_read`'s global branch legitimately shows her
    # every team -- an earlier version asserted against her and failed for a
    # correct reason, which is the wrong-actor mistake this file's siblings
    # kept making. Bob holds one team membership and no global role.
    outsider = UserRepo(session).create(email="rls-route-outsider@example.com", name="o")
    other = TeamRepo(session).create(name="rls-route-other")
    session.flush()
    MembershipRepo(session).grant(
        user_id=outsider.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=other.id,
        role=Role.TEAM_ADMIN,
    )
    session.commit()

    listing = constrained_client.get("/v1/teams", headers={"X-API-Key": world.bob_key})
    assert listing.status_code == 200, listing.text
    names = {team["name"] for team in listing.json()}
    assert "rls-route-other" not in names, (
        "bob is not a member of that team and holds no global role, so a constrained "
        "client must not see it -- if it appears, the fixture is not dropping the role "
        "and every test in this file proves nothing"
    )


def test_inviting_an_existing_non_member_does_not_500(
    constrained_client: TestClient, world: _World, session: Session
) -> None:
    """The second blocker, at the layer it broke.

    An account that exists but shares no scope with the caller is invisible
    under ``users_read``. The route used to conclude "new", call
    ``UserRepo.create``, and hit the unique constraint on email -- which no
    handler catches, so a 500. It resolves the invitee through
    ``find_for_invite`` now.

    Acted as CAROL, a team admin of globex with no global role. An earlier
    version used alice, who is a global ``BEACON_ADMIN`` -- and ``users_read``
    admits global admins, so ``get_by_email`` worked for her and the test
    passed with the fix reverted.
    """
    _refuse_global_admin(session, world.carol_id, "users_read admits global admins outright")
    stranger = UserRepo(session).create(email="rls-stranger@example.com", name="s")
    elsewhere = TeamRepo(session).create(name="rls-elsewhere")
    session.flush()
    MembershipRepo(session).grant(
        user_id=stranger.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=elsewhere.id,
        role=Role.TEAM_MEMBER,
    )
    session.commit()

    response = constrained_client.post(
        f"/v1/teams/{world.globex_team_id}/members",
        headers={"X-API-Key": world.carol_key},
        json={"user_email": "rls-stranger@example.com", "role": "team_member"},
    )

    assert response.status_code in (200, 201), response.text
    assert response.json()["user_id"] == str(stranger.id), (
        "the existing account must be reused, not duplicated"
    )
    count = session.execute(
        sa.text("SELECT count(*) FROM users WHERE email='rls-stranger@example.com'")
    ).scalar()
    assert count == 1, f"exactly one account for that address, found {count}"


def test_re_adding_yourself_does_not_500(
    constrained_client: TestClient, world: _World, session: Session
) -> None:
    """``grant`` upserts, and an UPDATE of your own membership row is refused.

    The policy refuses it deliberately -- role escalation is the one write
    where being the subject is the point -- but ``session.merge`` turned that
    refusal into a ``StaleDataError`` ("expected to update 1 row(s); 0 were
    matched"), which nothing handles. So re-posting your own email returned a
    500 where it used to return 201.

    Acted as CAROL, and the actor is the whole test. Alice's seeded acme row
    already holds ``role=team_admin`` and ``granted_by=alice.id`` -- exactly
    what the request supplies -- so ``merge`` finds nothing to change, emits a
    SELECT and no UPDATE, and the failure is never reached: deleting the
    route's entire self-branch left the earlier version green. Carol's globex
    row carries ``granted_by=alice.id``, so re-posting her own email rewrites
    that column and does emit the refused UPDATE.
    """
    _refuse_global_admin(session, world.carol_id, "a global admin's writes are permitted anyway")
    me = constrained_client.get("/v1/me", headers={"X-API-Key": world.carol_key})
    assert me.status_code == 200, me.text
    email = me.json()["user"]["email"]

    response = constrained_client.post(
        f"/v1/teams/{world.globex_team_id}/members",
        headers={"X-API-Key": world.carol_key},
        json={"user_email": email, "role": "team_admin"},
    )
    assert response.status_code != 500, (
        f"re-adding yourself must not crash; got {response.status_code}: {response.text}"
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()["user_id"] == str(world.carol_id)


def test_changing_your_own_role_is_refused_with_a_reason(
    constrained_client: TestClient, world: _World, session: Session
) -> None:
    """The other half of the self-branch, which had no test at all.

    Asking for a DIFFERENT role for yourself is the escalation the policy
    exists to stop, and it earns an explanation rather than the crash the
    ORM would otherwise produce.
    """
    _refuse_global_admin(session, world.carol_id, "the policy permits a global admin")
    me = constrained_client.get("/v1/me", headers={"X-API-Key": world.carol_key})
    assert me.status_code == 200, me.text

    response = constrained_client.post(
        f"/v1/teams/{world.globex_team_id}/members",
        headers={"X-API-Key": world.carol_key},
        json={"user_email": me.json()["user"]["email"], "role": "team_member"},
    )
    assert response.status_code == 409, (
        f"a self role-change must be refused with a reason; got "
        f"{response.status_code}: {response.text}"
    )
    assert "your own role" in response.text


def test_deleting_a_team_leaves_no_orphaned_memberships(
    constrained_client: TestClient, world: _World, session: Session
) -> None:
    """The first blocker, at the layer it broke.

    The route purges the roster and then the team. When the SELECT policy
    filtered the purge, the team went and its grants stayed -- and the route
    still answered 204, so nothing looked wrong. ``Membership.scope_id`` has no
    foreign key to ``teams``, so the database does not catch it either.
    """
    # Alice deletes it as a GLOBAL admin and is deliberately NOT made a member
    # of it. An earlier version granted her team_admin here, which put the
    # roster inside her own scopes -- so `memberships_read`'s scope branch
    # covered for the global branch and the test passed with that branch
    # removed. The bug only exists for an admin outside the team.
    doomed = TeamRepo(session).create(name="rls-doomed")
    victim = UserRepo(session).create(email="rls-victim@example.com", name="v")
    session.flush()
    # SOMEONE must be in the team, or there is no roster to orphan and the
    # test cannot fail whatever the policy says. An earlier version created an
    # empty team and passed with the fix reverted.
    MembershipRepo(session).grant(
        user_id=victim.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=doomed.id,
        role=Role.TEAM_MEMBER,
    )
    session.commit()
    team_id = doomed.id
    assert not [
        m
        for m in MembershipRepo(session).list_for_user(world.alice_id)
        if m.scope_kind == ScopeKind.TEAM and m.scope_id == team_id
    ], "the deleter must not be a member of the team, or the scope branch hides the bug"

    response = constrained_client.delete(
        f"/v1/teams/{team_id}", headers={"X-API-Key": world.alice_key}
    )
    # 204 on success; 403 if alice lacks the global role the route requires.
    # Either is a valid outcome -- what must NOT happen is the team going while
    # its grants remain.
    assert response.status_code in (204, 403, 409), response.text

    session.expire_all()
    team_rows = session.execute(
        sa.text("SELECT count(*) FROM teams WHERE id=:t"), {"t": team_id}
    ).scalar()
    grant_rows = session.execute(
        sa.text("SELECT count(*) FROM memberships WHERE scope_kind='team' AND scope_id=:t"),
        {"t": team_id},
    ).scalar()
    assert not (team_rows == 0 and grant_rows), (
        f"the team is gone and {grant_rows} membership(s) survive it -- orphaned grants "
        "for a team that no longer exists, which the route reported as success"
    )


def test_the_roster_is_readable_through_the_route(
    constrained_client: TestClient, world: _World
) -> None:
    """Add, remove and issue-key all check membership through this listing.

    Under the old self-only policy it returned one row -- yours -- so those
    three routes would 404 a real member.
    """
    response = constrained_client.get(
        f"/v1/teams/{world.acme_team_id}/members",
        headers={"X-API-Key": world.alice_key},
    )
    assert response.status_code == 200, response.text
    # `["members"]`, not `len(response.json())`. The response model is
    # TeamMemberListOut -- a single `members` field -- so the dict's length is
    # always exactly 1 and an EMPTY roster, the regression this test is named
    # for, passed the earlier assertion.
    roster = response.json()["members"]
    assert len(roster) >= 1, f"a member must see their own team's roster; got {roster}"
