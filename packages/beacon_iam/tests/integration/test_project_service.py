import pytest
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.errors import AuthorizationError
from beacon_iam.service.projects import ProjectService
from beacon_iam.service.teams import TeamService
from beacon_iam.service.users import UserService
from beacon_storage.models.tenancy import Role, ScopeKind, User
from beacon_storage.repository.memberships import MembershipRepo
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _bootstrap_admin(session: Session, email: str = "b@o.com") -> User:
    user = UserService(session).upsert_from_oidc(OidcClaims(subject=email, email=email, name="B"))
    MembershipRepo(session).grant(
        user_id=user.id,
        scope_kind=ScopeKind.GLOBAL,
        scope_id=user.id,
        role=Role.BEACON_ADMIN,
    )
    return user


def test_team_member_can_create_project(session: Session) -> None:
    admin = _bootstrap_admin(session, "team-admin@o.com")
    team = TeamService(session).create(actor_id=admin.id, name="t1")
    member = UserService(session).upsert_from_oidc(
        OidcClaims(subject="m1", email="m1@o.com", name="M")
    )
    MembershipRepo(session).grant(
        user_id=member.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=team.id,
        role=Role.TEAM_MEMBER,
    )

    project = ProjectService(session).create(
        actor_id=member.id,
        team_id=team.id,
        name="p1",
        description="d",
    )

    assert project.name == "p1"
    assert project.created_by == member.id


def test_non_member_cannot_create_project(session: Session) -> None:
    admin = _bootstrap_admin(session, "team-admin2@o.com")
    team = TeamService(session).create(actor_id=admin.id, name="t2")
    outsider = UserService(session).upsert_from_oidc(
        OidcClaims(subject="o1", email="o1@o.com", name="O")
    )

    with pytest.raises(AuthorizationError):
        ProjectService(session).create(
            actor_id=outsider.id,
            team_id=team.id,
            name="p2",
            description="",
        )


def test_create_project_grants_owner_to_creator(session: Session) -> None:
    admin = _bootstrap_admin(session, "team-admin3@o.com")
    team = TeamService(session).create(actor_id=admin.id, name="t3")
    member = UserService(session).upsert_from_oidc(
        OidcClaims(subject="m3", email="m3@o.com", name="M")
    )
    MembershipRepo(session).grant(
        user_id=member.id,
        scope_kind=ScopeKind.TEAM,
        scope_id=team.id,
        role=Role.TEAM_MEMBER,
    )

    project = ProjectService(session).create(
        actor_id=member.id,
        team_id=team.id,
        name="p3",
        description="",
    )
    memberships = MembershipRepo(session).list_for_user(member.id)
    project_role = next(
        (
            membership.role
            for membership in memberships
            if membership.scope_kind == ScopeKind.PROJECT and membership.scope_id == project.id
        ),
        None,
    )

    assert project_role == Role.PROJECT_OWNER
