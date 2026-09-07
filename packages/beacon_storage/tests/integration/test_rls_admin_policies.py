"""The tenant policies must permit administration and refuse escalation.

0026 rewrote ``memberships``, ``users``, ``teams`` and ``api_keys`` from a
single ``FOR ALL`` policy each into per-command policies, so that team
administration works under a constrained role. That is a security change, and
the whole of it lives in SQL predicates -- so these tests are the design.

Every case here was first run as a throwaway probe against the live cluster,
and two of them changed the design:

* a ``FOR ALL USING`` that let a member read their team's roster also let them
  DELETE it, admin included, leaving the team unadministered. That is why
  reads and writes have separate policies.
* a membership-aware policy ON ``memberships`` recursed -- Postgres raises
  "infinite recursion detected in policy for relation". That is why
  ``current_user_scopes()`` is a ``SECURITY DEFINER`` function.

Read as a pair with ``test_rls_regrade_events.py``, which covers the
tenant-data tables. This file covers the identity tables, where the
interesting failure is not a leak between teams but a privilege gained.
"""

from __future__ import annotations

import itertools
import uuid
from typing import TYPE_CHECKING, Any, NamedTuple, cast

import pytest
import sqlalchemy as sa
from beacon_iam.permissions import role_permissions
from beacon_storage.db import make_session_factory
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.rls import set_current_user
from sqlalchemy import text

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy import CursorResult
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class World(NamedTuple):
    """A global admin, a team admin, a plain member, and two teams."""

    global_admin: UUID
    team_admin: UUID
    member: UUID
    outsider: UUID
    team_a: UUID
    team_b: UUID


@pytest.fixture
def world(engine: Engine) -> World:
    factory = make_session_factory(engine)
    with factory() as s:
        users = {
            name: UserRepo(s).create(email=f"{name}@example.com", name=name)
            for name in ("gadmin", "tadmin", "member", "outsider")
        }
        team_a = TeamRepo(s).create(name="team-a")
        team_b = TeamRepo(s).create(name="team-b")
        s.flush()
        repo = MembershipRepo(s)
        repo.grant(
            user_id=users["gadmin"].id,
            scope_kind=ScopeKind.GLOBAL,
            scope_id=uuid.UUID(int=0),
            role=Role.BEACON_ADMIN,
        )
        repo.grant(
            user_id=users["tadmin"].id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team_a.id,
            role=Role.TEAM_ADMIN,
        )
        repo.grant(
            user_id=users["member"].id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team_a.id,
            role=Role.TEAM_MEMBER,
        )
        repo.grant(
            user_id=users["outsider"].id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team_b.id,
            role=Role.TEAM_MEMBER,
        )
        s.commit()
        return World(
            global_admin=users["gadmin"].id,
            team_admin=users["tadmin"].id,
            member=users["member"].id,
            outsider=users["outsider"].id,
            team_a=team_a.id,
            team_b=team_b.id,
        )


def _as(session: Session, actor: UUID) -> None:
    """Act as ``actor`` under the constrained role.

    Both halves matter. Without ``SET LOCAL ROLE`` the connection is the
    cluster owner and no policy is consulted, so every assertion below would
    pass against a schema with no policies at all.
    """
    session.execute(text("SET LOCAL ROLE beacon_app"))
    set_current_user(session, actor)


def _refused(session: Session, sql: str, **params: object) -> str:
    """Run a statement and report how the policy answered.

    Three outcomes, and the difference matters. ``WITH CHECK`` failures raise;
    ``USING`` failures on UPDATE/DELETE make the row invisible instead, so
    nothing is written and nothing raises. Both are refusals. Anything else is
    a write that happened.
    """
    savepoint = session.begin_nested()
    try:
        # `rowcount` lives on CursorResult, which is what a DML execute
        # returns; the base Result protocol does not declare it.
        result = cast("CursorResult[Any]", session.execute(text(sql), params))
        written = result.rowcount
        savepoint.rollback()
    except sa.exc.ProgrammingError as exc:
        savepoint.rollback()
        if "row-level security" in str(exc):
            return "refused-by-check"
        raise
    return "refused-by-using" if written == 0 else f"wrote {written}"


REFUSALS = ("refused-by-check", "refused-by-using")


# --------------------------------------------------------------------------
# escalation: each of these was a probe first
# --------------------------------------------------------------------------


def test_a_member_cannot_grant_themselves_into_another_team(engine: Engine, world: World) -> None:
    """The escalation the obvious ``WITH CHECK`` would have allowed.

    ``WITH CHECK (user_id = current_user_id())`` reads like a convenience --
    "you may write your own membership row" -- and is a privilege escalation:
    it lets anyone join any team.
    """
    with make_session_factory(engine)() as s:
        _as(s, world.member)
        assert (
            _refused(
                s,
                "INSERT INTO memberships (user_id,scope_kind,scope_id,role,"
                "created_at,updated_at) VALUES (:u,'team',:t,'team_admin',now(),now())",
                u=world.member,
                t=world.team_b,
            )
            in REFUSALS
        )


def test_a_member_cannot_escalate_their_own_role(engine: Engine, world: World) -> None:
    with make_session_factory(engine)() as s:
        _as(s, world.member)
        assert (
            _refused(
                s,
                "UPDATE memberships SET role='team_admin' WHERE user_id=:u",
                u=world.member,
            )
            in REFUSALS
        )


def test_a_member_cannot_delete_a_co_members_membership(engine: Engine, world: World) -> None:
    """The case that forced per-command policies.

    A member must be able to READ their team's roster -- add, remove and
    issue-key all check membership through it. Under one ``FOR ALL USING``
    that same predicate governed DELETE, so a member could remove the team's
    admin and leave it unadministered. Measured before the split.
    """
    with make_session_factory(engine)() as s:
        _as(s, world.member)
        assert (
            _refused(
                s,
                "DELETE FROM memberships WHERE user_id=:u AND scope_kind='team'",
                u=world.team_admin,
            )
            in REFUSALS
        )


def test_a_member_cannot_add_anyone_to_their_own_team(engine: Engine, world: World) -> None:
    """Reading the roster is not administering it.

    VALUES, not ``SELECT ... FROM users``. An earlier version selected the
    target row, which the acting member cannot SEE under ``users_read`` -- so
    the source set was empty, nothing was inserted, and this reported a
    refusal without ``memberships_insert``'s WITH CHECK ever being evaluated.
    Setting that check to ``(true)`` left it green. The foreign key still
    validates against the invisible user, because referential integrity is
    checked as the table owner.
    """
    with make_session_factory(engine)() as s:
        _as(s, world.member)
        result = _refused(
            s,
            "INSERT INTO memberships (user_id,scope_kind,scope_id,role,"
            "created_at,updated_at) VALUES (:u,'team',:t,'team_member',now(),now())",
            u=world.outsider,
            t=world.team_a,
        )
        assert result == "refused-by-check", (
            f"expected the WITH CHECK to refuse this, got {result!r}; "
            "a 'refused-by-using' here means the statement wrote nothing for "
            "some other reason and the check was never consulted"
        )


def test_a_team_admin_cannot_write_into_a_team_they_do_not_administer(
    engine: Engine, world: World
) -> None:
    with make_session_factory(engine)() as s:
        _as(s, world.team_admin)
        assert (
            _refused(
                s,
                "INSERT INTO memberships (user_id,scope_kind,scope_id,role,"
                "created_at,updated_at) SELECT id,'team',:t,'team_member',now(),now() "
                "FROM users WHERE id=:u",
                u=world.member,
                t=world.team_b,
            )
            in REFUSALS
        )


def test_a_team_admin_cannot_grant_themselves_global_admin(engine: Engine, world: World) -> None:
    """The escalation that would make every other refusal moot."""
    with make_session_factory(engine)() as s:
        _as(s, world.team_admin)
        assert (
            _refused(
                s,
                "INSERT INTO memberships (user_id,scope_kind,scope_id,role,"
                "created_at,updated_at) VALUES (:u,'global',:z,'beacon_admin',now(),now())",
                u=world.team_admin,
                z=uuid.UUID(int=0),
            )
            in REFUSALS
        )


def test_a_member_cannot_create_a_team(engine: Engine, world: World) -> None:
    """``TeamService.create`` requires GLOBAL_ADMIN, and the policy agrees."""
    with make_session_factory(engine)() as s:
        _as(s, world.member)
        assert (
            _refused(
                s,
                "INSERT INTO teams (id,name,created_at,updated_at) "
                "VALUES (:i,'sneaky',now(),now())",
                i=uuid.uuid4(),
            )
            in REFUSALS
        )


def test_a_member_cannot_mint_a_key_for_someone_else(engine: Engine, world: World) -> None:
    """A key authenticates AS its user, so this one is impersonation."""
    with make_session_factory(engine)() as s:
        _as(s, world.member)
        assert (
            _refused(
                s,
                "INSERT INTO api_keys (id,user_id,key_hash,label,created_at,updated_at) "
                "VALUES (:i,:u,'h','l',now(),now())",
                i=uuid.uuid4(),
                u=world.team_admin,
            )
            in REFUSALS
        )


def test_a_member_cannot_rename_another_user(engine: Engine, world: World) -> None:
    """Sharing a team lets you SEE someone, not edit them."""
    with make_session_factory(engine)() as s:
        _as(s, world.member)
        assert (
            _refused(s, "UPDATE users SET name='hijacked' WHERE id=:u", u=world.team_admin)
            in REFUSALS
        )


# --------------------------------------------------------------------------
# the operations that have to keep working
# --------------------------------------------------------------------------


def test_a_team_admin_can_invite_and_add_a_member(engine: Engine, world: World) -> None:
    """The real ``add_team_member`` flow: create the account, then grant it.

    This is what a plain ``WITH CHECK`` on the old policies refused, and it is
    the reason 0026 exists rather than being a tidy-up.

    TWO statements, because that is what the route does -- ``repo.create``
    then ``MembershipRepo.grant``. An earlier version of this test combined
    them into a data-modifying CTE for compactness and was refused on
    ``users``, while the same two inserts issued separately both succeed.
    Localised but NOT explained: the CTE is refused even with the memberships
    half removed, so it is the data-modifying CTE interacting with the policy
    rather than anything about the pair. Recorded rather than diagnosed
    because nothing in this codebase writes that way -- verified, no
    ``.cte(``, no ``WITH ... AS (INSERT`` anywhere outside tests -- so the
    honest note is that a test wrote SQL the app does not.
    """
    factory = make_session_factory(engine)
    invitee = uuid.uuid4()
    with factory() as s:
        _as(s, world.team_admin)
        created = _refused(
            s,
            "INSERT INTO users (id,email,name,is_active,created_at,updated_at) "
            "VALUES (:i,'invitee@example.com','inv',true,now(),now())",
            i=invitee,
        )
        assert created == "wrote 1", f"creating the invitee: {created}"

    # A fresh transaction, since the probe above rolled back: the grant needs
    # the user row to exist, which is the ordering the route relies on too.
    with factory() as s:
        _as(s, world.team_admin)
        s.execute(
            text(
                "INSERT INTO users (id,email,name,is_active,created_at,updated_at) "
                "VALUES (:i,'invitee2@example.com','inv',true,now(),now())"
            ),
            {"i": invitee},
        )
        granted = _refused(
            s,
            "INSERT INTO memberships (user_id,scope_kind,scope_id,role,"
            "created_at,updated_at) VALUES (:u,'team',:t,'team_member',now(),now())",
            u=invitee,
            t=world.team_a,
        )
        assert granted == "wrote 1", f"granting the membership: {granted}"


def test_a_global_admin_can_create_a_team(engine: Engine, world: World) -> None:
    """A new team has no membership yet, which is what made this hard.

    The old policy reused its membership predicate as the insert check, so a
    team could not be created by anyone -- its first member does not exist
    until after the row does.
    """
    with make_session_factory(engine)() as s:
        _as(s, world.global_admin)
        result = _refused(
            s,
            "INSERT INTO teams (id,name,created_at,updated_at) VALUES (:i,'fresh',now(),now())",
            i=uuid.uuid4(),
        )
        assert result == "wrote 1", result


def test_a_team_admin_can_mint_a_key_for_a_member(engine: Engine, world: World) -> None:
    """Permitted, and worth stating plainly: this is impersonation by design.

    It is how an invited person receives their first credential, gated by
    ``TEAM_MANAGE`` in the API. The policy matches that rather than being
    stricter or looser. If the product decides an admin should not be able to
    act as a member, it changes in both places.
    """
    with make_session_factory(engine)() as s:
        _as(s, world.team_admin)
        result = _refused(
            s,
            "INSERT INTO api_keys (id,user_id,key_hash,label,created_at,updated_at) "
            "VALUES (:i,:u,'h','l',now(),now())",
            i=uuid.uuid4(),
            u=world.member,
        )
        assert result == "wrote 1", result


# --------------------------------------------------------------------------
# reads: wide enough to administer, no wider
# --------------------------------------------------------------------------


def test_a_member_sees_their_own_teams_roster_and_no_other(engine: Engine, world: World) -> None:
    with make_session_factory(engine)() as s:
        _as(s, world.member)
        mine = s.execute(
            text("SELECT count(*) FROM memberships WHERE scope_id=:t"), {"t": world.team_a}
        ).scalar()
        theirs = s.execute(
            text("SELECT count(*) FROM memberships WHERE scope_id=:t"), {"t": world.team_b}
        ).scalar()
    assert mine == 2, f"the roster of my own team is what add/remove read: got {mine}"
    assert theirs == 0, "another team's roster must stay invisible"


def test_a_member_can_find_a_co_member_by_email(engine: Engine, world: World) -> None:
    """``get_by_email`` decides whether an invitee already has an account.

    Under the old self-only policy it returned nothing for anyone else, so an
    existing user looked new and the insert hit the unique constraint on
    email.
    """
    with make_session_factory(engine)() as s:
        _as(s, world.member)
        found = s.execute(
            text("SELECT count(*) FROM users WHERE email='tadmin@example.com'")
        ).scalar()
        stranger = s.execute(
            text("SELECT count(*) FROM users WHERE email='outsider@example.com'")
        ).scalar()
    assert found == 1, "a co-member must be findable"
    assert stranger == 0, "someone from another team must not be"


def test_api_key_hashes_stay_private_to_their_owner(engine: Engine, world: World) -> None:
    """Read is NOT widened to co-members. A key hash is nobody else's business.

    ``issue_member_key`` returns the plaintext it just generated rather than
    reading one back, so nothing needs this.
    """
    with make_session_factory(engine)() as s:
        _as(s, world.team_admin)
        s.execute(
            text(
                "INSERT INTO api_keys (id,user_id,key_hash,label,created_at,updated_at) "
                "VALUES (:i,:u,'h','l',now(),now())"
            ),
            {"i": uuid.uuid4(), "u": world.member},
        )
        s.commit()
    with make_session_factory(engine)() as s:
        _as(s, world.team_admin)
        seen = s.execute(
            text("SELECT count(*) FROM api_keys WHERE user_id=:u"), {"u": world.member}
        ).scalar()
    assert seen == 0, "an admin who can MINT a member's key must not be able to read it"


def test_a_global_admin_sees_every_team(engine: Engine, world: World) -> None:
    """``list_teams``' admin branch promises all teams; the policy now allows it."""
    with make_session_factory(engine)() as s:
        _as(s, world.global_admin)
        seen = s.execute(text("SELECT count(*) FROM teams")).scalar()
        assert seen is not None and seen >= 2, f"a global admin must see every team: {seen}"


# --------------------------------------------------------------------------
# escalation through the ROLE, which the first version of 0026 allowed
# --------------------------------------------------------------------------


@pytest.fixture
def global_viewer(engine: Engine) -> UUID:
    """A user holding a GLOBAL-scope ``viewer`` membership.

    Read-only in the app's permission map: ``Role.VIEWER`` maps to
    ``TEAM_VIEW`` and ``EVAL_VIEW`` and nothing else.
    """
    with make_session_factory(engine)() as s:
        user = UserRepo(s).create(email="gviewer@example.com", name="gv")
        s.flush()
        MembershipRepo(s).grant(
            user_id=user.id,
            scope_kind=ScopeKind.GLOBAL,
            scope_id=uuid.UUID(int=0),
            role=Role.VIEWER,
        )
        s.commit()
        return user.id


def test_a_global_scope_viewer_administers_nothing(
    engine: Engine, world: World, global_viewer: UUID
) -> None:
    """The scope is not the privilege, and the first version of 0026 confused them.

    ``IS_GLOBAL_ADMIN`` tested ``scope_kind = 'global'`` and never the role.
    Probed as ``beacon_app``, a global-scope VIEWER then created a team,
    promoted itself to ``beacon_admin``, and granted itself ``team_admin`` in
    an unrelated team -- three writes, all reported as successful. The app has
    always required the pair (``routes/teams.py``); the policy now does too.
    """
    factory = make_session_factory(engine)
    with factory() as s:
        _as(s, global_viewer)
        assert (
            _refused(
                s,
                "INSERT INTO teams (id,name,created_at,updated_at) "
                "VALUES (:i,'viewer-team',now(),now())",
                i=uuid.uuid4(),
            )
            in REFUSALS
        )
    with factory() as s:
        _as(s, global_viewer)
        assert (
            _refused(
                s,
                "UPDATE memberships SET role='beacon_admin' WHERE user_id=:u",
                u=global_viewer,
            )
            in REFUSALS
        )
    with factory() as s:
        _as(s, global_viewer)
        assert (
            _refused(
                s,
                "INSERT INTO memberships (user_id,scope_kind,scope_id,role,"
                "created_at,updated_at) VALUES (:u,'team',:t,'team_admin',now(),now())",
                u=global_viewer,
                t=world.team_a,
            )
            in REFUSALS
        )


def test_a_team_admin_cannot_rewrite_their_own_membership(engine: Engine, world: World) -> None:
    """The escalation that made the docstring's claim false.

    ``test_a_member_cannot_escalate_their_own_role`` acts as a plain member,
    which the policy always refused -- so the claim "escalating your own role
    is not permitted" was tested at the wrong privilege level. A team ADMIN
    passed ``ADMIN_OF_ROW`` for their own row, and nothing constrained the
    resulting role: probed, ``UPDATE memberships SET role='beacon_admin'``
    on their own row in their own team reported one row written, and
    ``effective_permissions`` maps team-scoped ``beacon_admin`` to every
    permission there is, GLOBAL_ADMIN included.

    Two rules close it, and both are asserted here: nobody edits their own
    membership row, and writing ``beacon_admin`` at all is reserved to a
    genuine global admin.
    """
    factory = make_session_factory(engine)
    with factory() as s:
        _as(s, world.team_admin)
        assert (
            _refused(
                s,
                "UPDATE memberships SET role='beacon_admin' WHERE user_id=:u AND scope_id=:t",
                u=world.team_admin,
                t=world.team_a,
            )
            in REFUSALS
        ), "an admin must not rewrite their own membership row"

    with factory() as s:
        _as(s, world.team_admin)
        assert (
            _refused(
                s,
                "UPDATE memberships SET role='beacon_admin' WHERE user_id=:u AND scope_id=:t",
                u=world.member,
                t=world.team_a,
            )
            in REFUSALS
        ), "nor promote a colleague to beacon_admin, which would hand it back"


def test_a_team_admin_cannot_mint_a_beacon_admin(engine: Engine, world: World) -> None:
    """Inserting the role is closed as well as updating into it.

    Otherwise the two-step is trivial: promote a confederate to
    ``beacon_admin``, who then promotes you.
    """
    with make_session_factory(engine)() as s:
        _as(s, world.team_admin)
        assert (
            _refused(
                s,
                "INSERT INTO memberships (user_id,scope_kind,scope_id,role,"
                "created_at,updated_at) VALUES (:u,'team',:t,'beacon_admin',now(),now())",
                u=world.outsider,
                t=world.team_a,
            )
            in REFUSALS
        )


def test_an_admin_cannot_edit_their_own_membership_even_harmlessly(
    engine: Engine, world: World
) -> None:
    """The self-edit ban, isolated from the role guard.

    Both rules block "admin rewrites own row to beacon_admin", so a test using
    that case passes with either one removed -- measured: deleting
    ``NOT_MY_OWN_ROW`` failed nothing. This uses a HARMLESS target role, so
    only the self-edit ban can refuse it.

    That the ban also stops a self-demotion is a real consequence and the
    intended one: the rule is blanket because "which self-edits are safe" is a
    judgement, and the row an admin is most able to write is their own. An
    admin who wants their role changed asks another admin -- including through
    ``add_team_member``, whose upsert would otherwise be the way round this.
    """
    with make_session_factory(engine)() as s:
        _as(s, world.team_admin)
        assert (
            _refused(
                s,
                "UPDATE memberships SET role='team_member' WHERE user_id=:u AND scope_id=:t",
                u=world.team_admin,
                t=world.team_a,
            )
            in REFUSALS
        ), "an admin must not edit their own membership row, harmless role or not"


# --------------------------------------------------------------------------
# routes that broke under the switch, found by review after the policies read
# correctly in isolation
# --------------------------------------------------------------------------


@pytest.fixture
def pure_global_admin(engine: Engine) -> UUID:
    """A global ``beacon_admin`` holding NO team membership.

    The distinction matters and hid a bug: the seeded admin in the local
    database holds both a global membership and a team one, so the scope
    branch of ``memberships_read`` covered for a missing global branch and a
    probe using that actor saw nothing wrong.
    """
    with make_session_factory(engine)() as s:
        user = UserRepo(s).create(email="pure-gadmin@example.com", name="pg")
        s.flush()
        MembershipRepo(s).grant(
            user_id=user.id,
            scope_kind=ScopeKind.GLOBAL,
            scope_id=uuid.UUID(int=0),
            role=Role.BEACON_ADMIN,
        )
        s.commit()
        return user.id


def test_deleting_a_team_takes_its_memberships_with_it(
    engine: Engine, world: World, pure_global_admin: UUID
) -> None:
    """``delete_team`` purges the roster then the team, and both must land.

    Postgres applies the SELECT policy when a DELETE's WHERE reads the
    relation, so a ``memberships_read`` that did not admit a global admin
    filtered the purge to nothing while ``teams_delete`` still permitted the
    team row. ``Membership.scope_id`` carries no foreign key to ``teams``, so
    the grants outlived their team and the route answered 204 -- success,
    reported over orphaned rows. Probed: 0 memberships deleted, 1 team.
    """
    with make_session_factory(engine)() as s:
        _as(s, pure_global_admin)
        roster = s.execute(
            text("SELECT count(*) FROM memberships WHERE scope_id=:t"), {"t": world.team_a}
        ).scalar()
        assert roster == 2, f"a global admin must SEE the roster it is about to purge; saw {roster}"

        purged = _refused(
            s,
            "DELETE FROM memberships WHERE scope_kind='team' AND scope_id=:t",
            t=world.team_a,
        )
        assert purged == "wrote 2", f"the purge must match the roster, got {purged}"


def test_an_invite_finds_an_existing_account_that_rls_hides(engine: Engine, world: World) -> None:
    """``add_team_member`` must not mistake an invisible account for a new one.

    ``users_read`` resolves self, co-members and global admins, so a team
    admin inviting an existing user who is not yet a co-member saw nothing,
    concluded "new", and hit the unique constraint on ``users.email`` --
    which no handler catches, so a 500.

    ``find_for_invite`` answers through a ``SECURITY DEFINER`` function. It
    returns the id and the active flag and nothing else, and refuses callers
    who administer nothing, so it cannot be used to test arbitrary addresses.
    """
    from beacon_storage.repository.users import UserRepo as Repo

    factory = make_session_factory(engine)
    with factory() as s:
        _as(s, world.team_admin)
        repo = Repo(s)
        assert repo.get_by_email("outsider@example.com") is None, (
            "the premise: the ordinary read cannot see this account"
        )
        found = repo.find_for_invite("outsider@example.com")
        assert found is not None, "the invite lookup must find it anyway"
        assert found.id == world.outsider
        assert found.is_active is True
        assert repo.find_for_invite("nobody-here@example.com") is None, (
            "and must still report a genuinely new address as new"
        )

    with factory() as s:
        _as(s, world.member)
        assert Repo(s).find_for_invite("outsider@example.com") is None, (
            "a caller who administers nothing must not be able to test addresses"
        )


def test_a_global_scope_viewer_cannot_mint_a_key(
    engine: Engine, world: World, global_viewer: UUID
) -> None:
    """The fourth surface, which the viewer test did not probe and so left open.

    ``test_a_global_scope_viewer_administers_nothing`` covers teams INSERT and
    memberships INSERT/UPDATE. ``api_keys_insert`` was not among them, and that
    is exactly why its unqualified ``scope_kind = 'global'`` survived the first
    tightening pass: nothing read it. A key authenticates AS its user, so this
    is the surface where the consequence is largest.

    ``global_viewer`` shares the global scope with ``world.global_admin``, so
    the shared-scope branch of the policy is satisfied and only the ROLE test
    can refuse this.
    """
    with make_session_factory(engine)() as s:
        _as(s, global_viewer)
        assert (
            _refused(
                s,
                "INSERT INTO api_keys (id,user_id,key_hash,label,created_at,updated_at) "
                "VALUES (:i,:u,'h','l',now(),now())",
                i=uuid.uuid4(),
                u=world.global_admin,
            )
            in REFUSALS
        )


def test_a_global_admin_outside_the_team_can_mint_a_members_key(
    engine: Engine, world: World, pure_global_admin: UUID
) -> None:
    """And the converse, which was a live 500.

    ``issue_member_key`` gates on ``TEAM_MANAGE``, which a global membership
    grants -- so a global admin who is not a member of the target's team
    reached ``ApiKeyRepo.create`` and the insert was refused, with no handler.
    The policy's shared-scope join could never match for them; the fix is a
    global-admin branch outside it.
    """
    with make_session_factory(engine)() as s:
        _as(s, pure_global_admin)
        result = _refused(
            s,
            "INSERT INTO api_keys (id,user_id,key_hash,label,created_at,updated_at) "
            "VALUES (:i,:u,'h','l',now(),now())",
            i=uuid.uuid4(),
            u=world.member,
        )
        assert result == "wrote 1", result


def test_the_acting_user_survives_a_mid_request_commit(engine: Engine, world: World) -> None:
    """A commit partway through a request must not turn RLS off.

    The GUC is transaction-LOCAL by design: a session-scoped one would leak
    the caller across requests on a pooled connection. But the ROLE comes from
    the DSN and persists, so before ``bind_rls`` existed a commit left the
    connection constrained and anonymous -- and every policy's
    ``current_user_id() IS NULL`` branch grants everything. Measured: a member
    who could see one team saw two after committing.

    ``delete_team``, ``remove_team_member`` and ``issue_member_key`` all commit
    partway through. None reads afterwards today, so nothing was broken yet --
    this pins the property before something does.
    """
    from beacon_storage.rls import bind_rls

    with make_session_factory(engine)() as s:
        bind_rls(s)
        _as(s, world.member)
        before = s.execute(text("SELECT count(*) FROM teams")).scalar()
        s.commit()

        # `bind_rls` restores the USER. It does not restore the ROLE, and here
        # that has to be re-applied by hand: `SET LOCAL ROLE` is also
        # transaction-scoped, so this connection reverts to the owning role on
        # commit -- whereas production keeps the serving role, which comes from
        # the DSN. So the fixture is weaker than production at exactly this
        # point, and comparing visibility without this line measures the
        # fixture's own artefact instead of the property under test. It read
        # 1 -> 2 before this was understood.
        assert s.execute(text("SELECT current_user")).scalar() == "beacon", (
            "the test connection is expected to revert to the owner on commit; "
            "if it does not, this comment is stale and the re-apply below is "
            "no longer needed"
        )
        s.execute(text("SET LOCAL ROLE beacon_app"))

        assert s.execute(text("SELECT current_user_id()")).scalar() is not None, (
            "the acting user was lost across the commit, so every policy now takes "
            "its NULL branch and grants everything"
        )
        after = s.execute(text("SELECT count(*) FROM teams")).scalar()
        assert after == before, f"visibility widened across a commit: {before} -> {after}"


def test_a_session_with_no_acting_user_is_left_alone(engine: Engine, world: World) -> None:
    """The auth endpoints run before a user is known, and must keep working.

    ``bind_rls`` re-applies only a RECORDED user. A version that always wrote
    the GUC would have to invent a value, and the auth path needs the NULL
    branch to look a user up at all.

    Takes ``world`` so there are users to resolve. Without it the table was
    empty and the count was 0 -- which the original ``is not None`` assertion
    accepted, so the test passed while proving nothing at all. Strengthening
    it to a positive count is what surfaced that.
    """
    from beacon_storage.rls import bind_rls

    with make_session_factory(engine)() as s:
        bind_rls(s)
        s.execute(text("SET LOCAL ROLE beacon_app"))
        assert s.execute(text("SELECT current_user_id()")).scalar() is None
        # A POSITIVE count. `count(*)` always returns an integer, so
        # `is not None` passed even when the read was denied and returned 0 --
        # measured: dropping the `current_user_id() IS NULL` branch from
        # `users_read` left this green while the auth path could resolve
        # nobody, which is the opposite of what its message claimed.
        visible = s.execute(text("SELECT count(*) FROM users")).scalar()
        assert visible and visible > 0, (
            f"the auth path must be able to resolve a user; it can see {visible}"
        )


# --------------------------------------------------------------------------
# 0027: a key may not carry privilege its issuer does not hold
# --------------------------------------------------------------------------


def test_the_sql_role_rank_matches_the_python_permission_lattice(engine: Engine) -> None:
    """``role_rank`` is only sound because these roles are a TOTAL order.

    ``may_issue_key_for`` compares ranks, and a rank is a faithful stand-in for
    "grants at least as much as" only while every pair of roles is comparable
    by permission set. Add a role that overlaps two others without containing
    either -- a billing role with its own permission, say -- and the rank
    silently starts asserting a containment that is not there.

    So this asserts the property the function depends on, in both directions,
    rather than the rank table it produces. It fails when someone adds a role,
    which is the moment somebody has to look.
    """
    ranks = {r: _scalar(engine, "SELECT role_rank(:r)", r=r.value) for r in Role}
    assert None not in ranks.values(), (
        f"role_rank does not know every Role: {ranks}. An unknown role returns NULL, "
        "which makes the comparison NULL and refuses -- safe, but it means no key can "
        "be issued for anyone holding that role."
    )
    for a, b in itertools.permutations(Role, 2):
        dominates = role_permissions(a) >= role_permissions(b)
        comparable = dominates or role_permissions(b) >= role_permissions(a)
        assert comparable, (
            f"{a.value} and {b.value} are not comparable by permission set, so the roles "
            "are no longer a total order and role_rank cannot represent them. "
            "may_issue_key_for needs a set comparison, not a rank."
        )
        assert (ranks[a] >= ranks[b]) == dominates, (
            f"role_rank says {a.value} >= {b.value} is {ranks[a] >= ranks[b]}, "
            f"but the permission sets say {dominates}"
        )
    # An unknown role must not read as the weakest one.
    assert _scalar(engine, "SELECT role_rank('billing')") is None


def _scalar(engine: Engine, sql: str, **params: object) -> Any:
    with make_session_factory(engine)() as s:
        return s.execute(text(sql), params).scalar()


def _may_issue(engine: Engine, issuer: UUID, target: UUID) -> bool:
    with make_session_factory(engine)() as s:
        _as(s, issuer)
        return bool(
            s.execute(text("SELECT may_issue_key_for(:i, :t)"), {"i": issuer, "t": target}).scalar()
        )


def test_may_issue_key_for_answers_privilege_containment(engine: Engine, world: World) -> None:
    """The whole rule, as a table. Each row was a probe against the app first."""
    # Contained: the member holds team-a and nothing else, and the team admin
    # administers team-a at a higher rank.
    assert _may_issue(engine, world.team_admin, world.member) is True

    # THE ESCALATION. The global admin's membership sits at a scope the team
    # admin does not administer, so a key for them would carry every tenant.
    assert _may_issue(engine, world.team_admin, world.global_admin) is False

    # Co-membership is not containment: the outsider's team-b membership is
    # outside the team admin's authority even though adding them to team-a
    # would make them share a scope.
    assert _may_issue(engine, world.team_admin, world.outsider) is False

    # A global admin already holds everything a key could carry.
    assert _may_issue(engine, world.global_admin, world.team_admin) is True

    # A plain member administers nothing, so they may issue for nobody -- their
    # OWN key goes through the policy's ownership branch, not this function.
    assert _may_issue(engine, world.member, world.member) is False

    # Nobody, holding nothing. Vacuously "no membership outside my scopes", so
    # a bare NOT EXISTS would permit it.
    assert _may_issue(engine, world.team_admin, uuid.uuid4()) is False


def test_the_insert_policy_refuses_a_key_for_a_user_with_outside_access(
    engine: Engine, world: World
) -> None:
    """The policy half, tested without the route, so neither can cover for the other.

    Timestamps are given explicitly and there is no RETURNING, deliberately:
    the ORM's `INSERT ... RETURNING` is read back through the SELECT policy, so
    a bare `INSERT` is what isolates the WITH CHECK from that.
    """
    with make_session_factory(engine)() as s:
        _as(s, world.team_admin)
        insert = (
            "INSERT INTO api_keys (id,user_id,key_hash,label,created_at,updated_at) "
            "VALUES (:i,:u,:h,'l',now(),now())"
        )
        assert (
            _refused(s, insert, i=uuid.uuid4(), u=world.global_admin, h=uuid.uuid4().hex)
            in REFUSALS
        ), "a team admin minted a key for a GLOBAL ADMIN"
        assert (
            _refused(s, insert, i=uuid.uuid4(), u=world.outsider, h=uuid.uuid4().hex) in REFUSALS
        ), "a team admin minted a key for a member of a team it does not administer"
        # And the permitted case is genuinely permitted, or the refusals above
        # would prove nothing about the rule -- only that the policy says no.
        assert (
            _refused(s, insert, i=uuid.uuid4(), u=world.member, h=uuid.uuid4().hex) == "wrote 1"
        ), "the ordinary case must still work"


def test_a_co_member_who_outranks_the_admin_cannot_have_a_key_issued(
    engine: Engine, world: World
) -> None:
    """The RANK half of the rule, which the scope test alone cannot reach.

    Each refusal above turns on something other than rank: the target holds a
    scope the issuer does not administer, or the issuer administers nothing at
    all, or the target holds no membership to check. This target defeats all
    three -- a ``beacon_admin`` scoped to the very team the admin administers --
    so only the rank comparison refuses it.

    Measured: with ``role_rank(mine.role) >= role_rank(t.role)`` deleted from
    ``may_issue_key_for``, every other assertion here stayed green. So the
    predicate had a clause no test could see, guarding a real escalation --
    ``_ROLE_PERMISSIONS[BEACON_ADMIN]`` is ``frozenset(Permission)``, and 0026
    notes that a team-scope beacon_admin collects every one of them, so a key
    for this member carries strictly more than its issuer holds.
    """
    with make_session_factory(engine)() as s:
        elevated = UserRepo(s).create(email="elevated@example.com", name="elevated")
        s.flush()
        MembershipRepo(s).grant(
            user_id=elevated.id,
            scope_kind=ScopeKind.TEAM,
            scope_id=world.team_a,
            role=Role.BEACON_ADMIN,
        )
        s.commit()
        elevated_id = elevated.id

    # Same scope, higher rank. The team admin administers team-a and this is
    # the target's ONLY membership, so nothing but the rank stands in the way.
    assert _may_issue(engine, world.team_admin, elevated_id) is False, (
        "a team admin issued a key for a beacon_admin of its own team, which carries "
        "every permission -- the rank comparison is not being consulted"
    )
    # The other direction holds: the elevated member may issue for the admin.
    assert _may_issue(engine, elevated_id, world.team_admin) is True

    with make_session_factory(engine)() as s:
        _as(s, world.team_admin)
        assert (
            _refused(
                s,
                "INSERT INTO api_keys (id,user_id,key_hash,label,created_at,updated_at) "
                "VALUES (:i,:u,:h,'l',now(),now())",
                i=uuid.uuid4(),
                u=elevated_id,
                h=uuid.uuid4().hex,
            )
            in REFUSALS
        )


def test_an_equal_rank_co_admin_may_still_have_a_key_issued(engine: Engine, world: World) -> None:
    """The rank comparison is ``>=``, and the boundary is load-bearing.

    Two admins of one team are peers: a key for either carries exactly what
    the other already holds, so there is nothing to escalate and refusing it
    would break the roster for the ordinary case of a second admin.

    Tested because the comparison is one character from wrong in a direction
    no other test can see. Measured: tightening ``>=`` to ``>`` left all 46
    tests green while turning this flow into a 403 whose message -- "holds
    access outside the scopes you administer" -- would have been false.
    """
    with make_session_factory(engine)() as s:
        peer = UserRepo(s).create(email="peer@example.com", name="peer")
        s.flush()
        MembershipRepo(s).grant(
            user_id=peer.id,
            scope_kind=ScopeKind.TEAM,
            scope_id=world.team_a,
            role=Role.TEAM_ADMIN,
        )
        s.commit()
        peer_id = peer.id

    assert _may_issue(engine, world.team_admin, peer_id) is True, (
        "two admins of one team are peers; a key for one carries nothing the other "
        "does not already hold"
    )
    with make_session_factory(engine)() as s:
        _as(s, world.team_admin)
        assert (
            _refused(
                s,
                "INSERT INTO api_keys (id,user_id,key_hash,label,created_at,updated_at) "
                "VALUES (:i,:u,:h,'l',now(),now())",
                i=uuid.uuid4(),
                u=peer_id,
                h=uuid.uuid4().hex,
            )
            == "wrote 1"
        )
