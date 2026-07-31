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
from beacon_storage.models.tenancy import ScopeKind, User  # noqa: TC002
from beacon_storage.repository.api_keys import ApiKeyRepo
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo
from beacon_storage.rls import set_current_user
from fastapi import Depends, Header, HTTPException, Path, status
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.config import ApiConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from beacon_storage.models.tenancy import Project, Team
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
        user = UserService(session).upsert_from_oidc(claims)
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


def get_project_or_404(
    project_id: Annotated[UUID, Path()],
    session: Annotated[Session, Depends(get_session)],
) -> Project:
    """Load the project referenced by the path parameter or raise 404."""
    project = ProjectRepo(session).get(project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"project {project_id} not found")
    return project


def require_permission(
    permission: Permission,
    *,
    scope_kind: str,
    path_param: str | None = None,
) -> Callable[..., User]:
    """Require the current user to hold a permission for a path-scoped resource."""
    target_scope = ScopeKind(scope_kind)
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
        project_team_id: UUID | None = None
        if target_scope == ScopeKind.PROJECT:
            project = ProjectRepo(session).get(scope_id)
            if project is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"project {scope_id} not found")
            project_team_id = project.team_id
        elif target_scope == ScopeKind.TEAM and TeamRepo(session).get(scope_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"team {scope_id} not found")

        permissions = effective_permissions(
            user.id,
            MembershipRepo(session).list_for_user(user.id),
            target_scope_kind=target_scope,
            target_scope_id=scope_id,
            target_project_team_id=project_team_id,
        )
        if permission not in permissions:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"missing permission: {permission.value}",
            )
        return user

    signature = inspect.signature(_dependency)
    parameters = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind is not inspect.Parameter.VAR_KEYWORD
    ]
    parameters.append(
        inspect.Parameter(
            resolved_path_param,
            inspect.Parameter.KEYWORD_ONLY,
            default=Path(...),
            annotation=UUID,
        )
    )
    _dependency.__signature__ = signature.replace(parameters=parameters)  # type: ignore[attr-defined]
    return _dependency
