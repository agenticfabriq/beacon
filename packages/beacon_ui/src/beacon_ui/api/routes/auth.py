from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from urllib.parse import urlencode, urlparse

from beacon_iam.auth.api_key import generate_api_key, hash_api_key
from beacon_iam.auth.oidc import OidcAuthCodeClient, OidcConfig, OidcVerifier
from beacon_iam.auth.password import verify_password
from beacon_iam.service.users import UserService
from beacon_storage.repository.api_keys import ApiKeyRepo
from beacon_storage.repository.users import UserRepo
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.config import ApiConfig
from beacon_ui.api.deps import get_session

if TYPE_CHECKING:
    from uuid import UUID

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class OidcExchangeIn(BaseModel):
    id_token: str
    label: str


class PasswordLoginIn(BaseModel):
    email: EmailStr
    password: str
    label: str


class ApiKeyOut(BaseModel):
    api_key: str


@router.post(
    "/oidc/exchange",
    response_model=ApiKeyOut,
    status_code=status.HTTP_201_CREATED,
    description="Required roles: public.",
    openapi_extra={"x-required-roles": ["public"]},
)
def oidc_exchange(
    body: OidcExchangeIn,
    session: Annotated[Session, Depends(get_session)],
) -> ApiKeyOut:
    """Exchange a verified OIDC ID token for a freshly issued API key."""
    config = ApiConfig()
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
    claims = verifier.verify_id_token(body.id_token)
    service = UserService(session)
    user = service.upsert_from_oidc(claims)
    service.grant_memberships_from_groups(user, claims)
    return _issue_key(session, user_id=user.id, label=body.label, prefix=config.api_key_prefix)


@router.post(
    "/password/login",
    response_model=ApiKeyOut,
    status_code=status.HTTP_201_CREATED,
    description="Required roles: public.",
    openapi_extra={"x-required-roles": ["public"]},
)
def password_login(
    body: PasswordLoginIn,
    session: Annotated[Session, Depends(get_session)],
) -> ApiKeyOut:
    """Authenticate an email/password pair and issue a new API key."""
    user = UserRepo(session).get_by_email(str(body.email))
    if (
        user is None
        or user.password_hash is None
        or not verify_password(body.password, user.password_hash)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    config = ApiConfig()
    return _issue_key(session, user_id=user.id, label=body.label, prefix=config.api_key_prefix)


@router.get(
    "/oidc/start",
    description="Required roles: public.",
    openapi_extra={"x-required-roles": ["public"]},
)
def oidc_start(
    request: Request,
    cli_callback: str | None = Query(default=None),
    cli_state: str | None = Query(default=None),
) -> RedirectResponse:
    """Begin an OIDC authorization-code flow and redirect to the IdP."""
    config = ApiConfig()
    if not config.oidc_issuer:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OIDC not configured")
    if cli_callback is not None and not _is_safe_loopback_url(cli_callback):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cli_callback must be a loopback URL")

    oidc_config = _oidc_config(config)
    state = OidcAuthCodeClient.fresh_state()
    redirect_uri = str(request.url_for("oidc_callback"))
    url = OidcAuthCodeClient(config=oidc_config).authorize_url(
        redirect_uri=redirect_uri,
        state=state,
    )
    response = RedirectResponse(url=url, status_code=307)
    response.set_cookie(
        "beacon_oidc_state",
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=config.cookies_secure,
    )
    if cli_callback is not None:
        response.set_cookie(
            "beacon_oidc_cli_callback",
            cli_callback,
            max_age=600,
            httponly=True,
            samesite="lax",
            secure=config.cookies_secure,
        )
    if cli_state is not None:
        response.set_cookie(
            "beacon_oidc_cli_state",
            cli_state,
            max_age=600,
            httponly=True,
            samesite="lax",
            secure=config.cookies_secure,
        )
    return response


@router.get(
    "/oidc/callback",
    name="oidc_callback",
    description="Required roles: public.",
    openapi_extra={"x-required-roles": ["public"]},
)
def oidc_callback(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    code: str = Query(...),
    state: str = Query(...),
    beacon_oidc_state: str | None = Cookie(default=None),
    beacon_oidc_cli_callback: str | None = Cookie(default=None),
    beacon_oidc_cli_state: str | None = Cookie(default=None),
) -> RedirectResponse:
    """Complete the OIDC flow, issue an API key, and redirect to the caller."""
    config = ApiConfig()
    if not config.oidc_issuer:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OIDC not configured")
    if not beacon_oidc_state or beacon_oidc_state != state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "state mismatch")

    oidc_config = _oidc_config(config)
    redirect_uri = str(request.url_for("oidc_callback"))
    id_token = OidcAuthCodeClient(config=oidc_config).exchange_code_for_id_token(
        code=code,
        redirect_uri=redirect_uri,
    )
    claims = OidcVerifier(oidc_config).verify_id_token(id_token)
    service = UserService(session)
    user = service.upsert_from_oidc(claims)
    service.grant_memberships_from_groups(user, claims)
    issued = _issue_key(
        session,
        user_id=user.id,
        label="browser-login",
        prefix=config.api_key_prefix,
    )

    if beacon_oidc_cli_callback and _is_safe_loopback_url(beacon_oidc_cli_callback):
        params = {"api_key": issued.api_key}
        if beacon_oidc_cli_state:
            params["state"] = beacon_oidc_cli_state
        separator = "&" if "?" in beacon_oidc_cli_callback else "?"
        target = f"{beacon_oidc_cli_callback}{separator}{urlencode(params)}"
        response = RedirectResponse(url=target, status_code=302)
        response.delete_cookie("beacon_oidc_state")
        response.delete_cookie("beacon_oidc_cli_callback")
        response.delete_cookie("beacon_oidc_cli_state")
        return response

    # No `beacon_api_key_once` cookie. It used to be set here: the freshly
    # minted key, `httponly=False` so a script could read it, for 120 seconds.
    # NOTHING READ IT -- not the page (which takes its key from `?api_key=` or
    # `localStorage`, see `index.html`), not a script, not the CLI. So it put a
    # live credential on a surface any script on this origin could reach, and
    # bought nothing for it.
    #
    # "Nothing" is a claim about the deployment as well as the repo, because
    # this redirect goes to `dashboard_url` when one is configured and a
    # dashboard shipped from elsewhere could have been the reader. Checked on
    # the instance, and checked under BOTH names: `dashboard_url` reads
    # `AliasChoices("BEACON_API_DASHBOARD_URL", "BEACON_DASHBOARD_URL")` and
    # the API-prefixed one wins, so testing only the shorter name would have
    # proved nothing. Both are unset in `/opt/beacon/.env` and in the
    # container's environment, so the target is `/ui` -- the page above.
    #
    # Removing it does not change what works, and it does not complete the
    # browser flow either: this redirect still hands the UI no key, which is a
    # gap in that flow rather than a reason to keep a credential where only an
    # attacker would find it.
    response = RedirectResponse(url=config.dashboard_url or "/ui", status_code=302)
    response.delete_cookie("beacon_oidc_state")
    return response


def _issue_key(session: Session, *, user_id: UUID, label: str, prefix: str) -> ApiKeyOut:
    key = generate_api_key(prefix=prefix)
    ApiKeyRepo(session).create(user_id=user_id, key_hash=hash_api_key(key), label=label)
    return ApiKeyOut(api_key=key)


def _oidc_config(config: ApiConfig) -> OidcConfig:
    return OidcConfig(
        issuer=config.oidc_issuer,
        client_id=config.oidc_client_id,
        client_secret=config.oidc_client_secret,
        jwks_uri=config.oidc_jwks_uri,
    )


def _is_safe_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and parsed.hostname in (
        "127.0.0.1",
        "localhost",
        "::1",
    )
