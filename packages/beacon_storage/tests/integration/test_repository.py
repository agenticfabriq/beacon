import pytest
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_user_repo_create_and_get_by_email(session: Session) -> None:
    repo = UserRepo(session)
    u = repo.create(email="dora@example.com", name="Dora")
    found = repo.get_by_email("dora@example.com")
    assert found is not None
    assert found.id == u.id


def test_team_repo_create_and_list(session: Session) -> None:
    repo = TeamRepo(session)
    repo.create(name="acme")
    repo.create(name="globex")
    names = sorted(t.name for t in repo.list())
    assert names == ["acme", "globex"]


def test_project_repo_create_in_team(session: Session) -> None:
    user = UserRepo(session).create(email="eve@example.com", name="Eve")
    team = TeamRepo(session).create(name="x")
    p = ProjectRepo(session).create(team_id=team.id, name="proj-1", created_by=user.id)
    assert p.team_id == team.id


def test_project_repo_unique_per_team(session: Session) -> None:
    user = UserRepo(session).create(email="frank@example.com", name="Frank")
    team = TeamRepo(session).create(name="y")
    ProjectRepo(session).create(team_id=team.id, name="dupe", created_by=user.id)
    with pytest.raises(IntegrityError):
        ProjectRepo(session).create(team_id=team.id, name="dupe", created_by=user.id)


def test_membership_repo_list_for_user(session: Session) -> None:
    user = UserRepo(session).create(email="gigi@example.com", name="Gigi")
    team = TeamRepo(session).create(name="z")
    proj = ProjectRepo(session).create(team_id=team.id, name="p", created_by=user.id)
    MembershipRepo(session).grant(
        user_id=user.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=team.id,
        role=Role.TEAM_MEMBER,
    )
    MembershipRepo(session).grant(
        user_id=user.id,
        scope_kind=ScopeKind.PROJECT,
        scope_id=proj.id,
        role=Role.PROJECT_OWNER,
    )
    ms = MembershipRepo(session).list_for_user(user.id)
    assert len(ms) == 2
