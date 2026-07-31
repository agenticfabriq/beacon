import pytest
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.errors import AuthorizationError, ConflictError
from beacon_iam.service.teams import TeamService
from beacon_iam.service.users import UserService
from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_team_admin_can_create_team(session: Session) -> None:
    user = UserService(session).upsert_from_oidc(
        OidcClaims(subject="admin", email="a@o.com", name="A")
    )
    MembershipRepo(session).grant(
        user_id=user.id,
        scope_kind=ScopeKind.GLOBAL,
        scope_id=user.id,
        role=Role.BEACON_ADMIN,
    )

    team = TeamService(session).create(actor_id=user.id, name="new-team")

    assert team.name == "new-team"


def test_unauthorized_user_cannot_create_team(session: Session) -> None:
    user = UserService(session).upsert_from_oidc(
        OidcClaims(subject="rando", email="r@o.com", name="R")
    )

    with pytest.raises(AuthorizationError):
        TeamService(session).create(actor_id=user.id, name="forbidden-team")


def test_duplicate_team_name_raises_conflict(session: Session) -> None:
    user = UserService(session).upsert_from_oidc(
        OidcClaims(subject="admin2", email="a2@o.com", name="A")
    )
    MembershipRepo(session).grant(
        user_id=user.id,
        scope_kind=ScopeKind.GLOBAL,
        scope_id=user.id,
        role=Role.BEACON_ADMIN,
    )
    TeamService(session).create(actor_id=user.id, name="dupe-team")

    with pytest.raises(ConflictError):
        TeamService(session).create(actor_id=user.id, name="dupe-team")
