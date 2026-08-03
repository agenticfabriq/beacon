"""User A's session must not see User B's suites when not shared."""

import pytest
from beacon_storage.db import make_session_factory
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.rls import set_current_user
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _use_app_role(session: Session) -> None:
    session.execute(text("SET LOCAL ROLE beacon_app"))


def test_rls_hides_other_teams_suites(engine: Engine) -> None:
    """Alice's team suite must not appear when querying as Bob."""
    factory = make_session_factory(engine)

    with factory() as su:
        alice = UserRepo(su).create(email="alice2@example.com", name="Alice")
        bob = UserRepo(su).create(email="bob2@example.com", name="Bob")
        team_a = TeamRepo(su).create(name="team-a")
        team_b = TeamRepo(su).create(name="team-b")
        p_a = SuiteRepo(su).create(
            team_id=team_a.id,
            name="alice-suite",
            description="",
            method="manual",
            suite_metadata={},
            created_by=alice.id,
        )
        SuiteRepo(su).create(
            team_id=team_b.id,
            name="bob-suite",
            description="",
            method="manual",
            suite_metadata={},
            created_by=bob.id,
        )
        MembershipRepo(su).grant(
            user_id=alice.id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team_a.id,
            role=Role.TEAM_ADMIN,
        )
        MembershipRepo(su).grant(
            user_id=bob.id,
            scope_kind=ScopeKind.TEAM,
            scope_id=team_b.id,
            role=Role.TEAM_ADMIN,
        )
        su.commit()
        alice_id = alice.id
        bob_id = bob.id

    with factory() as s:
        _use_app_role(s)
        set_current_user(s, bob_id)
        visible = SuiteRepo(s).list_for_team(p_a.team_id)
        assert visible == []

    with factory() as s:
        _use_app_role(s)
        set_current_user(s, alice_id)
        visible = SuiteRepo(s).list_for_team(p_a.team_id)
        assert len(visible) == 1
        assert visible[0].name == "alice-suite"
