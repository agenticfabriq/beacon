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

import contextlib
import re
from pathlib import Path
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
    bob_id: UUID
    bob_key: str
    carol_id: UUID
    carol_key: str
    acme_team_id: UUID
    globex_team_id: UUID


def test_the_client_really_is_constrained(
    constrained_client: TestClient, world: _World, session: Session
) -> None:
    """The fixture must actually drop the role, or every test below is theatre.

    The authoritative check is in ``constrained_session`` itself, which reads
    ``current_user`` and refuses to yield unless it is ``beacon_app``. This
    test covers what a route can show on top of that: ``/v1/me`` resolves the
    caller's own row, which needs both the GUC and the policy working, and a
    foreign team's roster comes back empty because the POLICY filters it rather
    than the route.
    """
    response = constrained_client.get("/v1/me", headers={"X-API-Key": world.alice_key})
    assert response.status_code == 200, response.text

    # The negative half is NOT asserted here, and that is the correction.
    # An earlier version listed teams as bob and claimed that seeing another
    # team's would mean "the fixture is not dropping the role and every test in
    # this file proves nothing" -- which was false: `list_teams` narrows to the
    # caller's own memberships in PYTHON, so the row is absent whether RLS is
    # enforced or not, and removing `SET LOCAL ROLE` from the fixture left all
    # six tests here green. A vacuous assertion advertising itself as the
    # detector is worse than none, because the next person to break the fixture
    # trusts it.
    #
    # The real detector is in the fixture: `constrained_session` reads
    # `current_user` and refuses to yield unless it is `beacon_app`. That
    # cannot be papered over by a route's own filtering, and removing the role
    # now fails every test in this file.
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

    # What CAN be asserted through a route: the roster of a team bob does not
    # belong to. That listing is filtered by the policy rather than in Python,
    # so it is decided by RLS -- and `require_permission` refusing first is an
    # equally correct outcome, hence either.
    roster = constrained_client.get(
        f"/v1/teams/{other.id}/members", headers={"X-API-Key": world.bob_key}
    )
    if roster.status_code == 200:
        assert roster.json()["members"] == [], (
            "bob belongs to no scope of that team, so its roster must come back "
            f"empty; got {roster.json()['members']}"
        )
    else:
        assert roster.status_code in (403, 404), roster.text


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


def test_the_production_session_dependency_binds_rls(api_client: TestClient, world: _World) -> None:
    """``get_session`` itself must register ``bind_rls``, not just the fixture.

    ``constrained_client`` overrides ``get_session`` wholesale, so nothing it
    does exercises the production dependency -- deleting ``bind_rls(session)``
    from ``deps.py`` left the entire suite green while production lost the
    acting user after every mid-request commit, which is the regression the
    other tests here were written to pin.

    So this drives the real dependency: set a user, commit, and check the
    acting user is still there. ``api_client`` is a parameter only to get the
    environment configured -- the assertions go through ``deps.get_session``.
    """
    import beacon_ui.api.deps as deps_mod
    from beacon_storage.rls import set_current_user

    sessions = deps_mod.get_session()
    session = next(sessions)
    try:
        session.execute(sa.text("SET LOCAL ROLE beacon_app"))
        set_current_user(session, world.alice_id)
        assert session.execute(sa.text("SELECT current_user_id()")).scalar() is not None

        session.commit()

        # The GUC is transaction-local, so this is where it would be gone.
        after = session.execute(sa.text("SELECT current_user_id()")).scalar()
        assert after is not None, (
            "get_session does not re-apply the acting user after a commit, so every "
            "policy takes its NULL branch for the rest of the request"
        )
        assert str(after) == str(world.alice_id)
    finally:
        session.rollback()
        session.close()
        # Drive the generator to completion so its own cleanup runs, rather
        # than calling .close() on it -- the declared return type is a plain
        # Iterator, which has no such method.
        with contextlib.suppress(StopIteration):
            next(sessions)


def test_the_fixture_reapplies_the_role_on_every_transaction() -> None:
    """``constrained_session`` must re-apply the role, not only set it once.

    ``SET LOCAL ROLE`` is transaction-scoped, so without a listener this
    connection reverts to the OWNER after any mid-request commit -- while
    production stays constrained, because its role comes from the DSN. A test
    of a route that read after committing would then pass here for the wrong
    reason.

    Pinned by READING the fixture, which is unsatisfying and is the honest
    option: no route reads after its commit today -- ``delete_team``,
    ``remove_team_member`` and ``issue_member_key`` each commit as their last
    statement -- so the behaviour cannot be reached through the API, and
    removing the listener failed nothing. This at least fails when the wiring
    goes, and should be replaced by a behavioural test the moment a route
    grows a read after its commit.
    """
    source = (Path(__file__).parents[1] / "conftest.py").read_text()
    body = re.search(
        r"def constrained_session\(\) -> Iterator\[Session\]:(.*?)\n    app\.",
        source,
        re.S,
    )
    assert body, "no constrained_session found in conftest"

    assert 'listens_for(session, "after_begin")' in body.group(1), (
        "constrained_session sets the role once, so it is lost on the first "
        "mid-request commit and everything after runs as the bypassing owner"
    )
    assert "bind_rls(session)" in body.group(1), (
        "constrained_session must register bind_rls as get_session does, or the "
        "acting user is lost across a commit here but not in production"
    )


# ---------------------------------------------------------------------------
# Issuing a key must not confer privilege the issuer does not hold.
#
# The chain these cover was measured end to end against the deployed app
# before the fix: a team admin holding NO global role added a global
# `beacon_admin` to their own team by email, minted a key for them, and
# authenticated as them -- and `effective_permissions` counts a global
# membership for every team, so that key administered every tenant.
#
# Each is run under BOTH clients, and it is worth being exact about what that
# does and does not prove, because the first version of this comment claimed a
# detector these tests do not have.
#
# It does NOT hold the policy against the route. `issue_member_key` refuses
# with 403 before it reaches `ApiKeyRepo.create`, so under `constrained_client`
# the negative cases never evaluate `api_keys_insert`'s WITH CHECK at all --
# measured: reverting the policy to 0026's permissive branch while keeping the
# route check leaves every test in this file green. The policy half is covered
# by `test_the_insert_policy_refuses_a_key_for_a_user_with_outside_access` in
# the storage suite, which inserts directly and cannot be shielded by a route.
#
# What it does prove: the route's refusal holds under either serving role, and
# `test_the_ordinary_case_still_works` passes under the CONSTRAINED one -- which
# is the assertion that caught the `INSERT ... RETURNING` trap, since the
# permitted path was refused under RLS while working perfectly as the owner.
# ---------------------------------------------------------------------------


def _mint(client: TestClient, world: _World, *, team_id: UUID, target_id: UUID) -> int:
    # `int(...)` because `TestClient.post` resolves as untyped here, so
    # `.status_code` is `Any` and mypy refuses to return it from an `-> int`
    # function. The inline `client.post(...).status_code` comparisons elsewhere
    # in this file never cross a typed function boundary, which is why this is
    # the only place it comes up.
    response = client.post(
        f"/v1/teams/{team_id}/members/{target_id}/api-keys",
        headers={"X-API-Key": world.carol_key},
        json={"label": "issued"},
    )
    return int(response.status_code)


@pytest.mark.parametrize("which", ["api_client", "constrained_client"])
def test_a_team_admin_cannot_mint_a_key_for_a_global_admin(
    which: str, request: pytest.FixtureRequest, world: _World, session: Session
) -> None:
    """The escalation, refused. Carol administers globex and nothing else."""
    client: TestClient = request.getfixturevalue(which)
    _refuse_global_admin(
        session,
        world.carol_id,
        "carol is the ATTACKER here; a global role would let her do this legitimately "
        "and the test would pass without the fix",
    )
    victim_email = session.execute(
        sa.text("SELECT email FROM users WHERE id = :i"), {"i": world.alice_id}
    ).scalar_one()
    # Alice is the victim precisely BECAUSE she holds a global beacon_admin
    # membership. If the fixture ever stops granting her one this test would
    # pass while proving nothing.
    held = session.execute(
        sa.text(
            "SELECT count(*) FROM memberships "
            "WHERE user_id = :i AND scope_kind = 'global' AND role = 'beacon_admin'"
        ),
        {"i": world.alice_id},
    ).scalar_one()
    assert held == 1, "the victim must hold the global role this escalation steals"

    added = request.getfixturevalue(which).post(
        f"/v1/teams/{world.globex_team_id}/members",
        headers={"X-API-Key": world.carol_key},
        json={"user_email": victim_email, "role": "team_member"},
    )
    assert added.status_code == 201, "adding a member is allowed; it is the KEY that is not"

    assert _mint(client, world, team_id=world.globex_team_id, target_id=world.alice_id) == 403


@pytest.mark.parametrize("which", ["api_client", "constrained_client"])
def test_sharing_one_team_is_not_enough_to_mint(
    which: str, request: pytest.FixtureRequest, world: _World, session: Session
) -> None:
    """Bob keeps his acme membership, which carol does not administer.

    This is the distinction the old policy missed: it permitted an insert when
    the target shared ANY scope the actor administered, and adding someone to
    your team makes that true by construction while leaving everything they
    hold elsewhere intact.
    """
    client: TestClient = request.getfixturevalue(which)
    _refuse_global_admin(session, world.carol_id, "carol must administer globex only")
    bob_email = session.execute(
        sa.text("SELECT email FROM users WHERE id = :i"), {"i": world.bob_id}
    ).scalar_one()
    added = client.post(
        f"/v1/teams/{world.globex_team_id}/members",
        headers={"X-API-Key": world.carol_key},
        json={"user_email": bob_email, "role": "team_member"},
    )
    assert added.status_code == 201

    assert _mint(client, world, team_id=world.globex_team_id, target_id=world.bob_id) == 403


@pytest.mark.parametrize("which", ["api_client", "constrained_client"])
def test_the_ordinary_case_still_works(
    which: str, request: pytest.FixtureRequest, world: _World, session: Session
) -> None:
    """An invitee whose ONLY membership is the team carol administers.

    The feature has to keep working, and under the constrained role this is
    also the test that the insert policy's admin branch is reachable at all --
    it was not, because the ORM's `INSERT ... RETURNING` met a SELECT policy
    restricted to the key's owner.
    """
    client: TestClient = request.getfixturevalue(which)
    added = client.post(
        f"/v1/teams/{world.globex_team_id}/members",
        headers={"X-API-Key": world.carol_key},
        json={"user_email": "newcomer@example.com", "role": "team_member"},
    )
    assert added.status_code == 201, added.text
    target_id = added.json()["user_id"]

    issued = client.post(
        f"/v1/teams/{world.globex_team_id}/members/{target_id}/api-keys",
        headers={"X-API-Key": world.carol_key},
        json={"label": "first credential"},
    )
    assert issued.status_code == 201, issued.text
    minted = issued.json()["api_key"]

    # And the key authenticates as the newcomer, carrying only their own
    # membership -- which is the point of the feature.
    me = client.get("/v1/me", headers={"X-API-Key": minted})
    assert me.status_code == 200, me.text
    assert me.json()["user"]["email"] == "newcomer@example.com"
    assert [m["scope_kind"] for m in me.json()["memberships"]] == ["team"]


@pytest.mark.parametrize("which", ["api_client", "constrained_client"])
def test_the_roster_cannot_grant_a_role_above_team_admin(
    which: str, request: pytest.FixtureRequest, world: _World, session: Session
) -> None:
    """`beacon_admin` is not expressible on this route, and that is the guard.

    Worth stating where the guard actually LIVES, because it is not in the
    handler: `TeamMemberIn.role` is `Literal["team_admin", "team_member"]`, so
    the ceiling is enforced by the request schema and a rank check in the
    handler would be unreachable code. A route check was written here first and
    removed for exactly that reason.

    So this test is pointed at the ceiling itself. If someone widens that
    Literal -- to add `viewer`, or `beacon_admin` -- this fails, and whoever
    does it has to decide deliberately whether a team admin may grant it. That
    matters because the roster is the one place a team admin names somebody
    else's role, and `beacon_admin` at team scope carries every permission.
    """
    client: TestClient = request.getfixturevalue(which)
    _refuse_global_admin(session, world.carol_id, "a global admin may grant anything")
    refused = client.post(
        f"/v1/teams/{world.globex_team_id}/members",
        headers={"X-API-Key": world.carol_key},
        json={"user_email": "inflated@example.com", "role": "beacon_admin"},
    )
    assert refused.status_code == 422, refused.text

    from typing import get_args

    from beacon_ui.api.schemas.team_member import TeamRole

    assert set(get_args(TeamRole)) == {"team_admin", "team_member"}, (
        "the roster's role ceiling moved; decide whether a team admin may grant the new one"
    )


@pytest.mark.parametrize("which", ["api_client", "constrained_client"])
def test_minting_your_own_key_reads_its_timestamp_back(
    which: str, request: pytest.FixtureRequest, world: _World, session: Session
) -> None:
    """The self-serve mint, under the serving role, including the read after commit.

    `create_api_key` returns `created_at`, and `ApiKeyCreatedOut` requires it.
    Since `ApiKey` no longer fetches server defaults through `INSERT ...
    RETURNING`, that value arrives from a lazy load AFTER `session.commit()` --
    so it is a SELECT in a NEW transaction, against `api_keys_read`, on a
    connection whose `app.current_user_id` was reset when the previous
    transaction ended.

    Every other test of this route uses the owner role, where no policy is
    consulted, so that refresh has never run against a policy at all.

    What this does NOT detect, stated because the first version of this
    docstring claimed it did: a `bind_rls` failure. Measured -- with the
    re-apply disabled so the refresh runs as an anonymous caller, this test
    stays green, because `api_keys_read` begins `current_user_id() IS NULL OR`
    and an anonymous read therefore sees everything. That branch is deliberate
    and 0025 argues for it, but it means a lost acting user FAILS OPEN on
    reads, and no assertion about a successful read can notice.

    So the claim is the narrow one: the self-serve mint works under the serving
    role, and the timestamp survives the loss of `INSERT ... RETURNING`.

    The STATUS is the whole guard. `record.created_at` is evaluated as an
    argument inside the handler, so a load that finds no row raises there and
    the route answers 500. An assertion that the returned field is non-empty
    adds nothing after that -- it cannot fail once the response exists -- and
    an earlier version of this test carried one with a message claiming it was
    the detector.
    """
    client: TestClient = request.getfixturevalue(which)
    _refuse_global_admin(
        session,
        world.carol_id,
        "may_issue_key_for permits a global admin outright, so a caller holding one "
        "satisfies api_keys_insert whatever the rest of it says",
    )
    response = client.post(
        "/v1/me/api-keys",
        headers={"X-API-Key": world.carol_key},
        json={"label": "self-serve"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["api_key"].startswith("bcn_")
