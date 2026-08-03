"""FastAPI dependencies for DB session, current user, and RLS binding."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from beacon_iam.auth.api_key import hash_api_key
from beacon_iam.auth.oidc import OidcConfig, OidcVerifier
from beacon_iam.errors import AuthenticationError
from beacon_iam.permissions import Permission, effective_permissions
from beacon_iam.service.users import UserService
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.api_keys import ApiKeyRepo
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.rls import set_current_user
from fastapi import Depends, Header, HTTPException, Path, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.config import ApiConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from beacon_storage.models.tenancy import Team
    from sqlalchemy.orm import sessionmaker

_factory: sessionmaker[Session] | None = None


def _config() -> ApiConfig:
    return ApiConfig()


def _get_factory() -> sessionmaker[Session]:
    global _factory  # noqa: PLW0603
    if _factory is None:
        config = _config()
        _factory = make_session_factory(make_engine(config.database_url))
    return _factory


def get_session() -> Iterator[Session]:
    """Yield a request-scoped SQLAlchemy session that commits on success."""
    factory = _get_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_current_user(
    session: Annotated[Session, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve the calling user from an API key or OIDC bearer token."""
    if x_api_key:
        repo = ApiKeyRepo(session)
        record = repo.get_by_hash(hash_api_key(x_api_key))
        if record is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key")
        repo.touch(record.id)
        user = UserRepo(session).get(record.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("user inactive or not found")
        set_current_user(session, user.id)
        return user

    if authorization and authorization.startswith("Bearer "):
        config = _config()
        if not config.oidc_issuer:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OIDC not configured")
        verifier = OidcVerifier(
            OidcConfig(
                issuer=config.oidc_issuer,
                client_id=config.oidc_client_id,
                client_secret=config.oidc_client_secret,
                jwks_uri=config.oidc_jwks_uri,
            )
        )
        claims = verifier.verify_id_token(authorization.removeprefix("Bearer ").strip())
        service = UserService(session)
        user = service.upsert_from_oidc(claims)
        service.grant_memberships_from_groups(user, claims)
        set_current_user(session, user.id)
        return user

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing credentials")


def get_team_or_404(
    team_id: Annotated[UUID, Path()],
    session: Annotated[Session, Depends(get_session)],
) -> Team:
    """Load the team referenced by the path parameter or raise 404."""
    team = TeamRepo(session).get(team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"team {team_id} not found")
    return team


def require_permission(
    permission: Permission,
    *,
    scope_kind: str,
    path_param: str | None = None,
) -> Callable[..., User]:
    """Require ``permission`` within the team that owns the path's resource.

    ``scope_kind`` names what the path identifies — "team", "suite" or "run" —
    and the dependency resolves it to the owning team. Permissions themselves
    are team-scoped: the team is the access boundary, and there is no narrower
    scope to check.
    """
    resolved_path_param = path_param or f"{scope_kind}_id"

    def _dependency(
        session: Annotated[Session, Depends(get_session)],
        user: Annotated[User, Depends(get_current_user)],
        **kwargs: object,
    ) -> User:
        raw_scope_id = kwargs.get(resolved_path_param)
        if raw_scope_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"missing path param {resolved_path_param}",
            )
        scope_id = UUID(str(raw_scope_id))

        if scope_kind == "team":
            if TeamRepo(session).get(scope_id) is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"team {scope_id} not found")
            team_id = scope_id
        elif scope_kind == "suite":
            suite = SuiteRepo(session).get(scope_id)
            if suite is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"suite {scope_id} not found")
            team_id = suite.team_id
        elif scope_kind == "run":
            run = RunRepo(session).get(scope_id)
            if run is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {scope_id} not found")
            team_id = run.team_id
        else:  # pragma: no cover - a route author error, not a request error
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "bad scope_kind")

        permissions = effective_permissions(
            user.id,
            MembershipRepo(session).list_for_user(user.id),
            team_id=team_id,
        )
        if permission not in permissions:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"missing permission: {permission.value}",
            )
        return user

    parameters = [
        inspect.Parameter(
            "session",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[Session, Depends(get_session)],
        ),
        inspect.Parameter(
            "user",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[User, Depends(get_current_user)],
        ),
        inspect.Parameter(resolved_path_param, inspect.Parameter.KEYWORD_ONLY, annotation=UUID),
    ]
    _dependency.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    return _dependency
