import pytest
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_upsert_from_oidc_creates_new_user(session: Session) -> None:
    svc = UserService(session)
    claims = OidcClaims(subject="user-42", email="dee@example.com", name="Dee")

    user = svc.upsert_from_oidc(claims)

    assert user.email == "dee@example.com"
    assert user.oidc_subject == "user-42"


def test_upsert_from_oidc_updates_existing(session: Session) -> None:
    svc = UserService(session)
    claims = OidcClaims(subject="user-42", email="dee@example.com", name="Dee")
    user1 = svc.upsert_from_oidc(claims)
    claims2 = OidcClaims(subject="user-42", email="dee@example.com", name="Dee Renamed")

    user2 = svc.upsert_from_oidc(claims2)

    assert user1.id == user2.id
    assert user2.name == "Dee Renamed"
