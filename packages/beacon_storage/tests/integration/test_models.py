import pytest
from beacon_storage.models.tenancy import (
    ApiKey,
    Membership,
    Project,
    Role,
    ScopeKind,
    Team,
    User,
)
from sqlalchemy.orm import Session


@pytest.mark.integration
def test_create_user(session: Session) -> None:
    u = User(email="alice@example.com", name="Alice")
    session.add(u)
    session.commit()
    assert u.id is not None
    assert u.created_at is not None


@pytest.mark.integration
def test_create_team_and_project(session: Session) -> None:
    u = User(email="owner@example.com", name="Owner")
    t = Team(name="acme")
    session.add_all([u, t])
    session.flush()
    p = Project(team_id=t.id, name="schema-linker-tuning", description="...", created_by=u.id)
    session.add(p)
    session.commit()
    assert p.team_id == t.id
    assert p.created_by == u.id


@pytest.mark.integration
def test_membership_team_scope(session: Session) -> None:
    u = User(email="bob@example.com", name="Bob")
    t = Team(name="globex")
    session.add_all([u, t])
    session.flush()
    m = Membership(user_id=u.id, scope_kind=ScopeKind.TEAM, scope_id=t.id, role=Role.TEAM_MEMBER)
    session.add(m)
    session.commit()
    assert m.role == Role.TEAM_MEMBER


@pytest.mark.integration
def test_api_key_hash_is_unique(session: Session) -> None:
    u = User(email="carol@example.com", name="Carol")
    session.add(u)
    session.flush()
    k1 = ApiKey(user_id=u.id, key_hash="hash-a", label="laptop")
    k2 = ApiKey(user_id=u.id, key_hash="hash-b", label="ci")
    session.add_all([k1, k2])
    session.commit()
    assert k1.id != k2.id
